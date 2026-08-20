#!/usr/bin/env python3
"""
serve.py — SuperRouter as an endpoint. This is the part an agent talks to.

    python3 -m superrouter.serve --port 8787
    # then point the agent at it:
    export OPENAI_BASE_URL=http://localhost:8787/v1
    export MIDSCENE_MODEL_BASE_URL=http://localhost:8787/v1
    export MIDSCENE_MODEL_NAME=superrouter/qa-vision-point

**How an agent declares what it is doing: in the model name.** Ask for
`superrouter/qa-vision-assert` and you get the cheapest model measured to hold
quality on judging; ask for `superrouter/qa-vision-point` and you get the one
measured to hold quality on clicking. They are different models, and the
measurements say so. `superrouter/auto` picks by looking at the request — an
image plus a coordinate-shaped instruction is a pointing call — and says which
task it inferred in the response headers so a wrong guess is visible rather than
silent.

Anything that is not a `superrouter/...` name passes straight through to the
upstream unchanged, so putting this in front of an agent cannot break a call it
does not understand.

**Why it runs locally and why that is not incidental.** It sits between the
agent and the provider, which is the most sensitive position in the stack: every
prompt and the key itself pass through it. Run on the user's own machine with
the user's own key, nothing leaves that was not already leaving. Hosted, it
would be a credential and prompt funnel for everyone using it. That is the line,
and it is why this ships as a local process with no remote mode.

**Every routed call is logged** — task, model, cost, latency, and what the
reference model would have cost. That log is how the saving stops being a claim
from a benchmark and becomes a number from production.
"""
import argparse
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .evals import endpoint_for
from .route_table import TASKS, latest, survives

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(CODE, "state", "served.jsonl")
POINT_HINT = re.compile(r"\bcoordinate|\bclick|\btap\b|\bpixel|x\s*,\s*y", re.I)


def build_table():
    """The routing table, read from the measurements rather than configured.

    A task whose set cannot separate the pool still routes — to the reference —
    because an unproven saving is not a saving. That is the same rule the policy
    applies offline, enforced here so a weak measurement cannot leak into
    production as a confident choice.
    """
    table = {}
    for task, cfg in TASKS.items():
        rows = latest(cfg["runs"], cfg["min_cases"])
        ref = next((r for r in rows if r["model"] == cfg["reference"]), None)
        if not ref:
            continue
        ok = [r for r in rows if r["model"] != ref["model"]
              and not survives(r, ref, cfg["axes"])]
        ok.sort(key=lambda r: r["cost_usd"])
        pick = ok[0] if ok else ref
        table[task] = {
            "model": pick["model"],
            "reference": ref["model"],
            "cost_per_case": pick["cost_usd"] / pick["cases"],
            "reference_cost_per_case": ref["cost_usd"] / ref["cases"],
            "evidence": {k: pick.get(k) for k, _ci, _d in cfg["axes"]},
            "cases": pick["cases"],
        }
    return table


def infer_task(body):
    """Guess the task when the caller said `auto`. Deliberately crude and
    deliberately visible in the response — a classifier is only worth building
    once there is traffic it cannot label, and that is a count, not a hunch."""
    msgs = body.get("messages") or []
    text, has_image = "", False
    for m in msgs:
        c = m.get("content")
        if isinstance(c, str):
            text += " " + c
        elif isinstance(c, list):
            for part in c:
                if part.get("type") == "text":
                    text += " " + (part.get("text") or "")
                elif part.get("type") == "image_url":
                    has_image = True
    if has_image:
        return "qa-vision-point" if POINT_HINT.search(text) else "qa-vision-assert"
    return "text-faithful"


class Handler(BaseHTTPRequestHandler):
    table = {}
    upstream_key = None
    lock = threading.Lock()

    def log_message(self, *a):
        pass                                    # the run log is the record

    def _send(self, code, payload, headers=None):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._send(200, {"object": "list", "data": [
                {"id": f"superrouter/{t}", "object": "model",
                 "owned_by": "superrouter", "routes_to": v["model"]}
                for t, v in self.table.items()
            ] + [{"id": "superrouter/auto", "object": "model", "owned_by": "superrouter"}]})
        elif self.path.rstrip("/") == "/table":
            self._send(200, self.table)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": "only /v1/chat/completions is served"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "body is not JSON"})
            return

        asked = body.get("model", "")
        routed, task = None, None
        if asked.startswith("superrouter/"):
            want = asked.split("/", 1)[1]
            task = infer_task(body) if want == "auto" else want
            entry = self.table.get(task)
            if not entry:
                self._send(400, {"error": f"no measurements for task '{task}'",
                                 "known": sorted(self.table)})
                return
            routed = entry["model"]
            body["model"] = routed
        # anything else passes through untouched

        url, key_override, wire = endpoint_for(body["model"])
        body["model"] = wire
        if routed:
            body.setdefault("usage", {"include": True})
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers={
            "Authorization": f"Bearer {key_override or self.upstream_key}",
            "Content-Type": "application/json",
            "X-Title": "superrouter",
        })
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            self._send(e.code, {"error": e.read().decode("utf-8", "replace")[:400]})
            return
        except Exception as e:
            self._send(502, {"error": str(e)[:300]})
            return
        took = time.time() - t0
        try:
            out = json.loads("\n".join(l for l in raw.splitlines()
                                       if not l.startswith(":")).strip())
        except json.JSONDecodeError:
            self._send(502, {"error": "upstream returned something that is not JSON"})
            return

        headers = {}
        if routed:
            entry = self.table[task]
            cost = float((out.get("usage") or {}).get("cost") or 0)
            headers = {"X-SuperRouter-Task": task,
                       "X-SuperRouter-Model": routed,
                       "X-SuperRouter-Reference": entry["reference"]}
            with self.lock:
                with open(LOG, "a") as f:
                    f.write(json.dumps({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "task": task, "asked": asked, "model": routed,
                        "inferred": asked.endswith("/auto"),
                        "cost_usd": cost, "seconds": round(took, 2),
                        "reference": entry["reference"],
                        "reference_cost_estimate": entry["reference_cost_per_case"],
                    }) + "\n")
        self._send(200, out, headers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1",
                    help="loopback only by default — see the note on why this "
                         "does not ship a remote mode")
    a = ap.parse_args()

    from .evals import key
    Handler.table = build_table()
    Handler.upstream_key = key()
    if not Handler.table:
        raise SystemExit("no measurements yet — run the ladders first")

    print(f"SuperRouter on http://{a.host}:{a.port}/v1\n")
    print(f"  {'task':<20} {'routes to':<44} {'saving vs reference':>19}")
    for t, v in Handler.table.items():
        mult = (v["reference_cost_per_case"] / v["cost_per_case"]
                if v["cost_per_case"] else 0)
        note = f"{mult:.0f}× cheaper" if v["model"] != v["reference"] else "— reference"
        print(f"  superrouter/{t:<8} {v['model']:<44} {note:>19}")
    print(f"\n  superrouter/auto     picks by looking at the request, and says which "
          f"task it chose\n  anything else        passes through untouched")
    print(f"\n  routed calls logged to {LOG}")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
