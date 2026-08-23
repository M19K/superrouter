#!/usr/bin/env python3
"""
pool.py — index the model pool from OpenRouter, and be honest about what an
index of prices can and cannot tell you.

    python3 -m superrouter.pool                    # refresh the snapshot, print a summary
    python3 -m superrouter.pool --vision           # only models that accept images
    python3 -m superrouter.pool --vision --top 20  # the twenty cheapest of those

**What this gives you: price. Not cost.** Price is $/million tokens, published,
and this reads it from the API rather than from memory. Cost is dollars per
finished task, and price alone cannot produce it, for three reasons this file
measures rather than asserts:

  1. Sixty-one of the vision models reason with reasoning that cannot be turned
     off. You pay for tokens you never asked for and cannot count in advance.
  2. Twelve separate pricing dimensions are in play across the pool - prompt,
     completion, image, cached read, cached write, internal reasoning, and more
     - and sixty-one models carry per-provider overrides on top.
  3. Tokenisers differ, so the same screenshot is a different number of tokens
     depending on who is looking at it.

Cost per task is therefore an **observation**, not a lookup. It comes out of the
same scored run that measures quality, which is why the index and the eval are
one project and not two.
"""
import argparse
import json
import os
import time
import urllib.request

from ._io import read_json

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(os.path.dirname(HERE), "state")
SNAPSHOT = os.path.join(STATE, "pool.json")
API = "https://openrouter.ai/api/v1/models"
PER_MODEL = "https://openrouter.ai/api/v1/models/{}/endpoints"


def fetch():
    """Read the live list. Never a cached figure and never a remembered one -
    prices here change weekly."""
    with urllib.request.urlopen(API, timeout=30) as r:
        return json.load(r)["data"]


def num(v):
    """Pricing values arrive as strings, and one field is a nested object."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def index(models):
    out = []
    for m in models:
        arch = m.get("architecture") or {}
        pricing = m.get("pricing") or {}
        prompt, completion = num(pricing.get("prompt")), num(pricing.get("completion"))
        # openrouter/auto prices itself at -1: it is a router, not a model.
        if prompt is None or prompt < 0:
            continue
        reasoning = m.get("reasoning") or {}
        out.append({
            "id": m["id"],
            "name": m.get("name"),
            "in_per_m": round(prompt * 1e6, 6),
            "out_per_m": round((completion or 0) * 1e6, 6),
            "image_per_k": round((num(pricing.get("image")) or 0) * 1e3, 6),
            "cache_read_per_m": round((num(pricing.get("input_cache_read")) or 0) * 1e6, 6),
            "context": m.get("context_length"),
            "max_out": (m.get("top_provider") or {}).get("max_completion_tokens"),
            "modalities_in": arch.get("input_modalities") or [],
            "vision": "image" in (arch.get("input_modalities") or []),
            "reasons": bool(reasoning),
            "reasoning_forced": bool(reasoning.get("mandatory")),
            "has_price_overrides": "overrides" in pricing,
            "supports": m.get("supported_parameters") or [],
        })
    return out


def save(rows):
    os.makedirs(STATE, exist_ok=True)
    payload = {"fetched": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(rows), "models": rows}
    with open(SNAPSHOT, "w") as f:
        json.dump(payload, f, indent=1)
    return SNAPSHOT


def load():
    with open(SNAPSHOT) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoints", action="store_true",
                    help="who actually serves each model we have measured, at what "
                         "precision, uptime and latency — the routing inputs a "
                         "golden set cannot see")
    ap.add_argument("--vision", action="store_true", help="only models that accept images")
    ap.add_argument("--top", type=int, default=15, help="how many of the cheapest to print")
    ap.add_argument("--offline", action="store_true", help="read the snapshot instead of the API")
    a = ap.parse_args()

    if a.endpoints:
        import glob

        from .evals import key as project_key
        measured = sorted({read_json(f)["summary"]["model"]
                           for d in ("runs", "runs_portfolio", "runs_midscene-docs",
                                     "point_runs", "text_runs")
                           for f in glob.glob(os.path.join(STATE, d, "*.json"))})
        measured = [m for m in measured if "/" in m and not m.startswith("local/")]
        report_endpoints(measured, project_key())
        return

    if a.offline:
        rows = load()["models"]
        print(f"snapshot from {load()['fetched']}")
    else:
        rows = index(fetch())
        print(f"fetched {len(rows)} priced models → {save(rows)}")

    pool = [r for r in rows if r["vision"]] if a.vision else rows
    label = "vision-capable" if a.vision else "all"
    forced = sum(r["reasoning_forced"] for r in pool)
    overrides = sum(r["has_price_overrides"] for r in pool)
    imgcharge = sum(r["image_per_k"] > 0 for r in pool)

    print(f"\n{len(pool)} {label} models")
    print(f"  {forced} reason with reasoning that cannot be turned off")
    print(f"  {overrides} carry per-provider price overrides")
    print(f"  {imgcharge} charge separately per image")

    pool.sort(key=lambda r: (r["in_per_m"], r["out_per_m"]))
    paid = [r for r in pool if r["in_per_m"] > 0]
    print(f"\n  cheapest {a.top} with a non-zero price:")
    print(f"  {'$/M in':>9} {'$/M out':>9} {'$/K img':>8}  {'ctx':>9}  model")
    for r in paid[:a.top]:
        flag = " ⟲" if r["reasoning_forced"] else ""
        print(f"  {r['in_per_m']:9.3f} {r['out_per_m']:9.3f} {r['image_per_k']:8.4f}  "
              f"{r['context']:>9}  {r['id']}{flag}")
    if paid:
        print(f"\n  price spread on input: {paid[0]['in_per_m']:.3f} → "
              f"{paid[-1]['in_per_m']:.2f} $/M — a factor of "
              f"{paid[-1]['in_per_m'] / paid[0]['in_per_m']:.0f}")
    print("\n  ⟲ = reasoning cannot be turned off, so its token burn is not yours to control")




# ─────────────────────────────────────────────────────────────────────────────
# Providers — the unit that actually serves a request
# ─────────────────────────────────────────────────────────────────────────────
#
# A model id is not one thing. `qwen/qwen3-vl-235b-a22b-instruct` is served by
# five providers at **bf16, fp8 and unknown** quantization, priced $0.20–$0.30,
# with uptime from 92.7% to 100%. OpenRouter picks one per request unless told
# otherwise.
#
# Two consequences, and the first is a correction to our own work:
#
# 1. **A score for a model name is an average over whichever providers happened
#    to serve those calls.** fp8 and bf16 are not the same model in any sense
#    that matters to quality, so a ladder that does not pin its provider is
#    measuring a moving target and cannot be reproduced.
# 2. **Uptime and latency are routing inputs a golden set cannot see.** A golden
#    set measures the quality of an answer that arrived. It is silent on whether
#    the endpoint was up, and a 92.7% endpoint fails one call in thirteen.


def endpoints(model, api_key):
    """Every provider serving one model, with what each actually offers."""
    req = urllib.request.Request(PER_MODEL.format(model),
                                 headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode())["data"]
    out = []
    for e in data.get("endpoints", []):
        out.append({
            "provider": e.get("provider_name"),
            "quantization": e.get("quantization") or "unspecified",
            "context": e.get("context_length"),
            "in_per_m": round(float((e.get("pricing") or {}).get("prompt") or 0) * 1e6, 4),
            "out_per_m": round(float((e.get("pricing") or {}).get("completion") or 0) * 1e6, 4),
            "uptime_30m": e.get("uptime_last_30m"),
            "latency_30m": e.get("latency_last_30m"),
            "status": e.get("status"),
        })
    return sorted(out, key=lambda e: (-(e["uptime_30m"] or 0), e["in_per_m"]))


def report_endpoints(models, api_key):
    print(f"{'model':<44} {'prov':>4} {'quantization':<20} {'price $/M':<14} "
          f"{'uptime':<16} split?")
    print("-" * 108)
    risky = []
    for m in models:
        try:
            eps = endpoints(m, api_key)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"{m:<44} — {str(e)[:50]}")
            continue
        if not eps:
            continue
        qs = sorted({e["quantization"] for e in eps})
        pr = sorted(e["in_per_m"] for e in eps)
        up = [e["uptime_30m"] for e in eps if e["uptime_30m"] is not None]
        split = len(qs) > 1
        if split:
            risky.append((m, qs))
        print(f"{m:<44} {len(eps):>4} {','.join(qs):<20} "
              f"${pr[0]:<5.2f}–${pr[-1]:<5.2f} "
              f"{(min(up) if up else 0):>5.1f}–{(max(up) if up else 0):<5.1f}%  "
              f"{'YES — pin it' if split else ''}")
    if risky:
        print("\nThese are served at more than one numeric precision, so a score "
              "for the bare\nmodel name is an average over whichever provider "
              "answered. Pin the provider\nbefore measuring, and record which one "
              "the score belongs to:")
        for m, qs in risky:
            print(f"   {m}  ({', '.join(qs)})")

if __name__ == "__main__":
    main()
