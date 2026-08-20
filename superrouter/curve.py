#!/usr/bin/env python3
"""
curve.py — the held-quality curve. Read every scored run and put cost next to
quality, because either number alone decides nothing.

    python3 -m superrouter.curve                 # the curve, newest run per model
    python3 -m superrouter.curve --reference anthropic/claude-sonnet-5

**What the columns mean.** `catch` is the share of injected defects the model
saw; it is what QA is for. `false alarm` is how often it called a healthy screen
broken; a model with a high one produces reports nobody trusts. `accuracy` is
both together over the whole set and is here for orientation, not for deciding.

**vs ref** is the cost of one run as a fraction of the reference model's — the
number the whole project is about.
"""
import argparse
import glob
import json
import os

RUNS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "runs")


def latest_per_model(with_results=False):
    """One row per model, the newest complete run. Filenames start with the run
    stamp, so sorting by name sorts by time and the last write wins."""
    best = {}
    for path in sorted(glob.glob(os.path.join(RUNS, "*.json"))):
        blob = json.load(open(path))
        s = blob["summary"]
        if s["cases"] < 100:         # partial runs are smoke tests, not scores
            continue
        best[s["model"]] = (s, blob["results"]) if with_results else s
    return list(best.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", default="anthropic/claude-sonnet-5")
    a = ap.parse_args()

    rows = latest_per_model()
    if not rows:
        raise SystemExit(f"no complete runs in {RUNS}")
    ref = next((r for r in rows if r["model"] == a.reference), None)
    rows.sort(key=lambda r: -r["cost_usd"])

    print(f"held-quality curve · {len(rows)} models · {rows[0]['cases']} cases each · "
          f"constant-answer baseline 50% accuracy\n")
    head = (f"{'$ / run':>9} {'vs ref':>7} {'catch (95% CI)':>18} "
            f"{'false alarm (95% CI)':>23} {'acc':>5}  model")
    print(head)
    print("-" * len(head))
    for r in rows:
        share = f"{r['cost_usd'] / ref['cost_usd'] * 100:5.1f}%" if ref and ref["cost_usd"] else "   — "
        mark = "  ← reference" if ref and r["model"] == ref["model"] else ""
        cc = r.get("catch_ci") or (0, 100)
        fc = r.get("false_alarm_ci") or (0, 100)
        refusal = f"  ⚠ refused {r['refusal_pct']}%" if r.get("refusal_pct") else ""
        print(f"{r['cost_usd']:9.5f} {share:>7} "
              f"{r['catch']:>9}%  ({cc[0]:>2}-{cc[1]:<2})  "
              f"{r['false_alarm']:>11}%  ({fc[0]:>2}-{fc[1]:<2})  "
              f"{r['accuracy']:4}%  {r['model']}{mark}{refusal}")

    if not ref:
        return
    print(f"\nreference: {ref['model']} — catch {ref['catch']}%, "
          f"false alarms {ref['false_alarm']}%, ${ref['cost_usd']:.5f} per run")
    qualify = [r for r in rows
               if r["catch"] >= ref["catch"] and r["false_alarm"] <= ref["false_alarm"]
               and r["model"] != ref["model"]]
    if qualify:
        cheapest = min(qualify, key=lambda r: r["cost_usd"])
        print(f"\nmatch or beat the reference on BOTH numbers: "
              f"{', '.join(r['model'] for r in qualify)}")
        print(f"cheapest of those: {cheapest['model']} at ${cheapest['cost_usd']:.5f} — "
              f"{ref['cost_usd'] / cheapest['cost_usd']:.0f}x cheaper than the reference")
    else:
        print("\nNothing matches the reference on both numbers at once. That is the "
              "finding, not a failure of the run — say so rather than relaxing the bar "
              "until something passes.")


if __name__ == "__main__":
    main()
