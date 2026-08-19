#!/usr/bin/env python3
"""
pool.py — index the model pool from OpenRouter, and be honest about what an
index of prices can and cannot tell you.

    python3 -m router.pool                    # refresh the snapshot, print a summary
    python3 -m router.pool --vision           # only models that accept images
    python3 -m router.pool --vision --top 20  # the twenty cheapest of those

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

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(os.path.dirname(HERE), "state")
SNAPSHOT = os.path.join(STATE, "pool.json")
API = "https://openrouter.ai/api/v1/models"


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
    ap.add_argument("--vision", action="store_true", help="only models that accept images")
    ap.add_argument("--top", type=int, default=15, help="how many of the cheapest to print")
    ap.add_argument("--offline", action="store_true", help="read the snapshot instead of the API")
    a = ap.parse_args()

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


if __name__ == "__main__":
    main()
