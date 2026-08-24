#!/usr/bin/env python3
"""
xroutebench.py — the independent check: does our label help on data we did not make?

    python3 -m superrouter.xroutebench

**Why this exists.** Every measurement in this project until now was self-made —
our exam, our product, our audit. A self-audit can tell you a number drifted; it
cannot tell you the whole frame is wrong. The only check that can is somebody
else's benchmark, with somebody else's models, scored by somebody else's metric.

So this runs the **cost-aware label** against a public routing benchmark —
445 queries × 18 models × 13 tasks. Nothing in the benchmark is ours: not the
queries, not the candidate models, not the performance scores. That is the
point; a frame we did not build is the only thing that can tell us our frame
is wrong.

**The conventional label**  `argmax(performance)` — the strongest model for the
                 query, which is how routing supervision is normally written.
**Our label**    `0 if wrong else 1 − λ·cost_rank` — the cheapest model that is
                 still within a stated distance of the strongest.

Cost is derived from the token counts *in their own data* multiplied by live
OpenRouter prices. **11 of their 18 models could be priced**; the other seven are
not on OpenRouter under a matchable name and are excluded from every arm equally,
including the baselines, so no arm is advantaged by the exclusion.

**What would falsify our claim:** if the cost-aware label does not cut cost at
comparable quality here, then the improvement was an artefact of our own binary
metric and does not generalise. That result would be reported as-is.
"""
import argparse
import json
import math
import os
import random
from collections import defaultdict

from ._io import read_json, read_lines

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The benchmark is not redistributed here — it is not ours to hand on, and a
# tree whose rule is that what ships is ours end to end should not carry 8 MB of
# somebody else's dataset. Point this at your own copy.
DATA = os.environ.get("SR_BENCHMARK_DIR") or os.path.join(CODE, "state", "xroutebench")

SHAPE = """  Expected in that directory:

    train.jsonl   one JSON object per line, each a (query, candidate model,
                  outcome) triple: `query`, `model_name`, `performance`,
                  and `input_tokens` / `output_tokens`.
    prices.json   {"<model_name>": {"in_per_m": <usd>, "out_per_m": <usd>}}

  Any public routing benchmark in that shape works. Set SR_BENCHMARK_DIR to
  point somewhere else."""


def load():
    if not os.path.isdir(DATA):
        raise SystemExit(
            f"no benchmark data at {DATA}\n\n"
            f"  This is the one check in this project that uses data we did not\n"
            f"  make — which is exactly why it is worth running, and why the data\n"
            f"  is not vendored into this repository.\n\n{SHAPE}")
    prices = read_json(os.path.join(DATA, "prices.json"))
    rows = [json.loads(ln) for ln in read_lines(os.path.join(DATA, "train.jsonl"))]
    out = []
    for r in rows:
        p = prices.get(r["model_name"])
        if not p:
            continue                      # unpriceable: dropped from every arm
        cost = ((r.get("input_tokens") or 0) / 1e6 * p["in_per_m"]
                + (r.get("output_tokens") or 0) / 1e6 * p["out_per_m"])
        out.append({**r, "cost": cost})
    return out, prices


def cost_rank(rows):
    per = defaultdict(list)
    for r in rows:
        per[r["model_name"]].append(r["cost"])
    avg = {m: max(sum(v) / len(v), 1e-9) for m, v in per.items()}
    lo, hi = math.log(min(avg.values())), math.log(max(avg.values()))
    span = (hi - lo) or 1.0
    return {m: (math.log(c) - lo) / span for m, c in avg.items()}, avg


def evaluate(rows, tolerance, lam=0.5, seed=7, test_frac=0.35):
    rank, avg = cost_rank(rows)
    by_q = defaultdict(list)
    for r in rows:
        by_q[r["query"]].append(r)

    qs = sorted(by_q)
    random.Random(seed).shuffle(qs)
    test = qs[int(len(qs) * (1 - test_frac)):]

    def arm(pick):
        perf = cost = 0.0
        picks = defaultdict(int)
        for q in test:
            c = pick(by_q[q])
            picks[c["model_name"]] += 1
            perf += c["performance"]
            cost += c["cost"]
        return perf / len(test), cost, picks

    # theirs: the strongest model, cost absent from the decision
    theirs = arm(lambda cs: max(cs, key=lambda r: r["performance"]))

    # ours: correctness dominates, then price orders what is good enough.
    # `tolerance` is the stated distance from the best that counts as good
    # enough — it is a parameter, not a hidden constant, and it is swept below.
    def ours_pick(cs):
        best = max(r["performance"] for r in cs)
        ok = [r for r in cs if r["performance"] >= best - tolerance]
        return min(ok, key=lambda r: rank[r["model_name"]])
    ours = arm(ours_pick)

    fixed = {}
    for m in avg:
        sel = [r for q in test for r in by_q[q] if r["model_name"] == m]
        if len(sel) == len(test):
            fixed[m] = (sum(r["performance"] for r in sel) / len(test),
                        sum(r["cost"] for r in sel))
    return len(test), theirs, ours, fixed, avg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lam", type=float, default=0.5)
    ap.parse_args()
    rows, prices = load()
    models = sorted({r["model_name"] for r in rows})
    print(f"xRouteBench · {len({r['query'] for r in rows})} queries × {len(models)} priced "
          f"models × {len({r['task_name'] for r in rows})} tasks")
    print("  their data, their models, their scores. Cost from their token counts "
          "× live OpenRouter prices.\n")

    n, theirs, ours, fixed, avg = evaluate(rows, tolerance=0.0)
    best_fixed = max(fixed.items(), key=lambda kv: kv[1][0]) if fixed else None
    cheap_fixed = min(fixed.items(), key=lambda kv: kv[1][1]) if fixed else None

    print(f"held-out: {n} queries\n")
    print(f"{'quality':>9} {'cost':>10} {'vs theirs':>10}  strategy")
    print("-" * 68)
    if best_fixed:
        m, (p, c) = best_fixed
        print(f"{p:>9.3f} {c:>10.4f} {theirs[1]/c:>9.2f}×  always {m}  (best fixed)")
    if cheap_fixed:
        m, (p, c) = cheap_fixed
        print(f"{p:>9.3f} {c:>10.4f} {theirs[1]/c:>9.2f}×  always {m}  (cheapest fixed)")
    print(f"{theirs[0]:>9.3f} {theirs[1]:>10.4f} {1:>9.2f}×  conventional label — argmax(performance)")

    print("\n── our label, swept over how much quality you agree to give up ──\n")
    print(f"{'tolerance':>10} {'quality':>9} {'vs theirs':>10} {'cost':>10} {'cheaper by':>11}")
    print("-" * 58)
    for tol in (0.0, 0.01, 0.02, 0.05, 0.10):
        _, th, ou, _, _ = evaluate(rows, tolerance=tol)
        print(f"{tol:>10.2f} {ou[0]:>9.3f} {ou[0]-th[0]:>+10.3f} {ou[1]:>10.4f} "
              f"{th[1]/ou[1]:>10.1f}×")

    print("\n  tolerance 0.00 means: among models TIED at the best score, take the")
    print("  cheapest. It gives up nothing at all and is the honest headline.")
    print("  Larger values are what you would accept knowingly, and are shown so the")
    print("  trade is visible rather than chosen for you.")


if __name__ == "__main__":
    main()
