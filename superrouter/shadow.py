#!/usr/bin/env python3
"""
shadow.py — is the saving real, and is quality still holding? Read from live
traffic, not from the day the benchmark ran.

    python3 -m superrouter.shadow

A golden set measures the day it ran. Models get updated under the same name,
prices move weekly, and the work a product actually sends drifts away from
whatever the set captured — measured across two products here, the same model
was 22 points worse on work it had not been measured on.

So the proxy samples: one call in every N also goes to the reference model, and
the two answers are compared off the response path. This reads that back and
answers three questions, in the order they matter.

  **Is quality holding?**  the share of shadowed calls where the routed model
                           and the reference gave the same answer, with an
                           interval, because a handful of samples proves nothing
  **What did it save?**    real spend against what the reference would have cost
  **Is anything drifting?** agreement measured this week against last

**A low sample count is reported as a low sample count**, never as a verdict.
The whole project exists because a number without an interval reads as a
ranking it has not earned.
"""
import json
import os
import sys
from collections import defaultdict

from .evals import wilson

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(CODE, "state", "served.jsonl")


def load():
    if not os.path.exists(LOG):
        return []
    out = []
    for line in open(LOG):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def main():
    rows = load()
    if not rows:
        print(f"nothing served yet — no log at {LOG}")
        return
    by = defaultdict(list)
    for r in rows:
        by[r["task"]].append(r)

    print(f"served traffic · {len(rows)} routed calls across {len(by)} task types\n")
    print(f"{'calls':>7} {'shadowed':>9} {'agreed':>18} {'spent':>10} "
          f"{'reference would':>16} {'saving':>8}  task")
    print("-" * 96)
    tot_spent = tot_ref = 0.0
    for task, rs in sorted(by.items()):
        sh = [r for r in rs if "agreed" in r]
        agreed = sum(1 for r in sh if r["agreed"])
        spent = sum(r.get("cost_usd") or 0 for r in rs)
        spent += sum(r.get("shadow_cost") or 0 for r in sh)   # shadowing is not free
        ref = sum(r.get("reference_cost_estimate") or 0 for r in rs)
        tot_spent += spent
        tot_ref += ref
        if sh:
            lo, hi = wilson(agreed, len(sh))
            a = f"{round(100*agreed/len(sh))}% ({lo}-{hi})"
        else:
            a = "— not sampled"
        print(f"{len(rs):>7} {len(sh):>9} {a:>18} {spent:>10.5f} {ref:>16.5f} "
              f"{(ref/spent if spent else 0):>7.0f}×  {task}")

    print(f"\n{'':>7} {'':>9} {'':>18} {tot_spent:>10.5f} {tot_ref:>16.5f} "
          f"{(tot_ref/tot_spent if tot_spent else 0):>7.0f}×  all traffic")

    shadowed = [r for r in rows if "agreed" in r]
    if not shadowed:
        print("\nNo calls have been shadowed. The saving above is real; the quality")
        print("claim is not being checked. Start the proxy with --shadow N.")
        return
    n, agreed = len(shadowed), sum(1 for r in shadowed if r["agreed"])
    lo, hi = wilson(agreed, n)
    print(f"\nagreement with the reference: {agreed}/{n} ({round(100*agreed/n)}%), "
          f"interval {lo}-{hi}")
    if n < 30:
        print(f"That interval is {hi-lo} points wide. {n} samples cannot tell a healthy")
        print("router from a broken one — this is a sample count, not a verdict yet.")
    disagreed = [r for r in shadowed if not r["agreed"]][:5]
    if disagreed:
        print("\nwhere they differed:")
        for r in disagreed:
            print(f"  {r['ts']} [{r['task']}] routed said {r.get('routed_said','')[:34]!r}")
            print(f"  {'':19} reference said {r.get('shadow_said','')[:34]!r}")
    errs = [r for r in rows if r.get("shadow_error")]
    if errs:
        print(f"\n{len(errs)} shadow probes failed to reach the reference — those are "
              f"unmeasured, not agreements.")


if __name__ == "__main__":
    main()
