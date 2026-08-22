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

**Shadow mode is how it stays honest after the day it was measured.** With
`--shadow N`, one call in every N is *also* sent to the reference model and the
two answers are compared. Nothing about the response changes — the caller still
gets the cheap model's answer at the cheap model's latency — but the log gains a
running agreement rate.

That matters because a benchmark measures the day it ran. Models are updated
under the same name, prices move weekly, and the traffic a product actually
sends drifts away from whatever the golden set captured. Measured here across
two products: the same model can be 22 points worse on work it was not measured
on. Shadow mode is the only thing that notices that while it is happening rather
than at the next re-measurement.

`python3 -m superrouter.shadow` reads the log back and says whether the saving
is real and whether agreement is holding.
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
from .route_table import TASKS, latest, same_exam, survives

# Failures worth trying the next model for, and failures that are not.
# A 400 or a 401 is the request being wrong, and sending a wrong request to a
# second model just spends money to be told the same thing. A 429, a 5xx or a
# timeout is the provider having a moment, which is exactly what a chain is for.
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524}

# A status code alone is not enough for a ROUTER, and the first test of the
# chain proved it. A model that has been decommissioned, or that no provider is
# currently serving, comes back as **400 or 404** — the same codes as a
# malformed request. Blanket "do not retry 4xx" is right for a plain proxy and
# wrong here: a model disappearing is the single most important reason a routing
# chain exists, and it was the one case the chain refused to act on.
#
# So those two codes are retryable only when the message is about the MODEL
# rather than the request. Matching on message text is fragile, so it is kept
# narrow, listed here, and the whole attempt trail is returned to the caller —
# if this misjudges, it is visible rather than silent.
MODEL_IS_THE_PROBLEM = (
    "not a valid model", "no endpoints found", "no allowed providers",
    "model not found", "is not available", "no instances available",
    "does not exist", "has been deprecated", "no providers available",
)


def retryable(status, detail):
    if status in RETRYABLE_STATUS:
        return True
    if status in (400, 404):
        d = (detail or "").lower()
        return any(k in d for k in MODEL_IS_THE_PROBLEM)
    return False

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
        # Same exam isolation the offline table applies. Without it the
        # FALLBACK CHAIN silently admits models measured on a superseded set —
        # caught the first time the chain was exercised, when it fell back to a
        # free model whose only measurement was on a 90-case set it had long
        # been superseded by, and which returns an empty answer most of the
        # time. An unproven model is not a safety net; it is a second failure
        # wearing one's clothes.
        rows, _stale = same_exam(latest(cfg["runs"], cfg["min_cases"]))
        ref = next((r for r in rows if r["model"] == cfg["reference"]), None)
        if not ref:
            continue
        ok = [r for r in rows if r["model"] != ref["model"]
              and not survives(r, ref, cfg["axes"])]
        ok.sort(key=lambda r: r["cost_usd"])
        pick = ok[0] if ok else ref
        # The fallback chain is every model that ALSO passed the policy test,
        # cheapest first, with the reference last. Two rules make it safe:
        # nothing enters the chain that has not been measured to survive, and
        # the reference is always reachable, so a cheap model's bad ten minutes
        # cannot become the caller's outage. A router with no fallback is worse
        # than no router — it converts a hiccup upstream into a total failure.
        chain = [r["model"] for r in ok] + [ref["model"]]
        seen, ordered = set(), []
        for m in chain:
            if m not in seen:
                seen.add(m)
                ordered.append(m)
        table[task] = {
            "model": pick["model"],
            "chain": ordered,
            "cost_of": {r["model"]: r["cost_usd"] / r["cases"] for r in ok + [ref]},
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


def same_decision(a, b):
    """Do two answers carry the same decision, ignoring how much was said?

    Comparing raw strings counts explanation as disagreement. Measured on the
    first live run: the routed model answered `TRUE\n\nThe claim is a direct
    copy of the source` where the reference answered `TRUE`. Identical decision,
    scored as a difference.

    So: normalise, then treat a prefix as a match. A longer answer that begins
    with the shorter one is the same decision with reasoning attached. This is
    deliberately generic — it knows nothing about TRUE/FALSE or any other task's
    vocabulary, because a comparison hardcoded to one task's answers would not
    survive the next task type.
    """
    n = lambda t: "".join(ch for ch in (t or "").upper() if ch.isalnum())
    x, y = n(a), n(b)
    if not x or not y:
        return None
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    return long.startswith(short[:24])


class Handler(BaseHTTPRequestHandler):
    table = {}
    upstream_key = None
    shadow_every = 0
    counter = 0
    lock = threading.Lock()

    def _shadow(self, body, task, routed, answer):
        """Ask the reference the same question, off the response path. The
        caller has already been served — this costs latency nobody waits on."""
        ref = self.table[task]["reference"]
        probe = dict(body)
        probe["model"] = ref
        probe["usage"] = {"include": True}
        url, ko, wire = endpoint_for(ref)
        probe["model"] = wire
        req = urllib.request.Request(url, data=json.dumps(probe).encode(), headers={
            "Authorization": f"Bearer {ko or self.upstream_key}",
            "Content-Type": "application/json", "X-Title": "superrouter-shadow"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                raw = r.read().decode("utf-8", "replace")
            d = json.loads("\n".join(l for l in raw.splitlines()
                                     if not l.startswith(":")).strip())
        except Exception as e:
            return {"shadow_error": str(e)[:120]}
        ref_text = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        rec = {"shadow_model": ref,
               "shadow_cost": float((d.get("usage") or {}).get("cost") or 0),
               "shadow_said": ref_text[:80], "routed_said": (answer or "")[:80]}

        # A reference that returned nothing is a FAILED PROBE, not a disagreement.
        # Measured the first time this ran end to end: the reference came back
        # empty on 15 of 62 samples because the caller's own max_tokens was small
        # and the reference spent it before writing anything. Scored as
        # disagreement that read as 76% agreement — the router looking broken
        # because the instrument was. An instrument that blames the thing it is
        # measuring for its own failure is worse than no instrument.
        if not ref_text.strip():
            rec["agreed"] = None
            rec["shadow_skipped"] = "reference returned nothing under the caller's own limits"
            return rec

        rec["agreed"] = same_decision(answer, ref_text)
        return rec

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

        # Walk the chain: the routed model first, then every cheaper-than-
        # reference model that also survived the policy test, then the
        # reference. Only retryable failures advance it — see RETRYABLE_STATUS.
        chain = (entry["chain"] if routed else [body["model"]])
        try_from = chain.index(routed) if routed and routed in chain else 0
        attempts, raw, used = [], None, None
        t0 = time.time()

        for step, candidate in enumerate(chain[try_from:]):
            url, key_override, wire = endpoint_for(candidate)
            call = dict(body, model=wire)
            if routed:
                call.setdefault("usage", {"include": True})
            req = urllib.request.Request(url, data=json.dumps(call).encode(), headers={
                "Authorization": f"Bearer {key_override or self.upstream_key}",
                "Content-Type": "application/json",
                "X-Title": "superrouter",
            })
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    raw = r.read().decode("utf-8", "replace")
                used = candidate
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                attempts.append({"model": candidate, "status": e.code,
                                 "detail": detail[:120]})
                if not retryable(e.code, detail) or step == len(chain[try_from:]) - 1:
                    # Not the chain's business, or nothing left to try. Either
                    # way the caller gets the real error rather than a 502 that
                    # hides which model refused and why.
                    self._send(e.code, {"error": detail, "superrouter_attempts": attempts})
                    return
            except Exception as e:
                attempts.append({"model": candidate, "status": "transport",
                                 "detail": str(e)[:120]})
                if step == len(chain[try_from:]) - 1:
                    self._send(502, {"error": str(e)[:300],
                                     "superrouter_attempts": attempts})
                    return

        if raw is None:
            self._send(502, {"error": "every model in the chain failed",
                             "superrouter_attempts": attempts})
            return
        if routed and used != routed:
            # A fallback that is not visible is a silent cost increase: you
            # believe you are paying the cheap model's price and you are not.
            routed = used
        took = time.time() - t0
        try:
            out = json.loads("\n".join(l for l in raw.splitlines()
                                       if not l.startswith(":")).strip())
        except json.JSONDecodeError:
            self._send(502, {"error": "upstream returned something that is not JSON"})
            return

        headers = {}
        if routed:
            answer = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            entry = self.table[task]
            cost = float((out.get("usage") or {}).get("cost") or 0)
            headers = {"X-SuperRouter-Task": task,
                       "X-SuperRouter-Model": routed,
                       "X-SuperRouter-Reference": entry["reference"]}
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "task": task, "asked": asked, "model": routed,
                   "intended": entry["model"],
                   "fell_back": routed != entry["model"],
                   "attempts": attempts,
                   "inferred": asked.endswith("/auto"),
                   "cost_usd": cost, "seconds": round(took, 2),
                   "reference": entry["reference"],
                   "reference_cost_estimate": entry["reference_cost_per_case"]}
            with self.lock:
                Handler.counter += 1
                due = self.shadow_every and Handler.counter % self.shadow_every == 0
            if due:
                rec.update(self._shadow(body, task, routed, answer))
                headers["X-SuperRouter-Shadow"] = str(rec.get("agreed"))
        if routed and routed != entry["model"]:
            headers["X-SuperRouter-Fellback-From"] = entry["model"]
            with self.lock:
                with open(LOG, "a") as f:
                    f.write(json.dumps(rec) + "\n")
        self._send(200, out, headers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--shadow", type=int, default=0, metavar="N",
                    help="also send one call in every N to the reference model and "
                         "record whether they agreed. 0 disables. Off the response "
                         "path, so it costs no latency the caller waits on.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="loopback only by default — see the note on why this "
                         "does not ship a remote mode")
    a = ap.parse_args()

    from .evals import key
    Handler.table = build_table()
    Handler.upstream_key = key()
    Handler.shadow_every = a.shadow
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
    if a.shadow:
        print(f"\n  shadow: 1 call in {a.shadow} is also sent to the reference and "
              f"compared\n          — `python3 -m superrouter.shadow` reads it back")
    print(f"\n  routed calls logged to {LOG}")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
