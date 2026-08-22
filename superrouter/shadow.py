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
    # The saving and the cost of proving it are two different numbers. Rolled
    # together, a run with shadow at 1-in-1 reports 1x — the router looking
    # worthless because the audit was billed to it. Kept apart on purpose.
    print(f"{'calls':>7} {'shadowed':>9} {'agreed':>18} {'routed':>9} {'checks':>8} "
          f"{'reference would':>16} {'saving':>8}  task")
    print("-" * 96)
    tot_spent = tot_ref = 0.0
    for task, rs in sorted(by.items()):
        sh = [r for r in rs if r.get("agreed") is not None]
        agreed = sum(1 for r in sh if r["agreed"])
        spent = sum(r.get("cost_usd") or 0 for r in rs)
        checks = sum(r.get("shadow_cost") or 0 for r in rs)   # the audit, billed separately
        ref = sum(r.get("reference_cost_estimate") or 0 for r in rs)
        tot_spent += spent
        tot_checks = globals().setdefault("_tc", 0) + checks
        globals()["_tc"] = tot_checks
        tot_ref += ref
        if sh:
            lo, hi = wilson(agreed, len(sh))
            a = f"{round(100*agreed/len(sh))}% ({lo}-{hi})"
        else:
            a = "— not sampled"
        print(f"{len(rs):>7} {len(sh):>9} {a:>18} {spent:>9.5f} {checks:>8.5f} "
              f"{ref:>16.5f} {(ref/spent if spent else 0):>7.0f}×  {task}")

    tot_checks = globals().get("_tc", 0.0)
    print(f"\n{'':>7} {'':>9} {'':>18} {tot_spent:>9.5f} {tot_checks:>8.5f} "
          f"{tot_ref:>16.5f} {(tot_ref/tot_spent if tot_spent else 0):>7.0f}×  all traffic")
    if tot_checks:
        print(f"\n  The router saved {(tot_ref/tot_spent if tot_spent else 0):.0f}×. "
              f"Proving it cost ${tot_checks:.5f} on top, at the sampling rate you ran.")
        print(f"  At 1-in-20 instead of 1-in-1 that check would be "
              f"${tot_checks/20:.5f}, and the saving is unchanged.")

    shadowed = [r for r in rows if r.get("agreed") is not None]
    if not shadowed:
        print("\nNo calls have been shadowed. The saving above is real; the quality")
        print("claim is not being checked. Start the proxy with --shadow N.")
        return
    n, agreed = len(shadowed), sum(1 for r in shadowed if r["agreed"])
    lo, hi = wilson(agreed, n)
    skipped = [r for r in rows if r.get("shadow_skipped")]
    if skipped:
        print(f"\n{len(skipped)} probe(s) returned nothing from the reference and are "
              f"excluded from the rate.\n  Not a disagreement — the reference could not "
              f"answer under the caller's own limits.\n  Counting them as disagreement "
              f"is how this read 76% on its first live run.")
    fb = [r for r in rows if r.get("fell_back")]
    if fb:
        # A fallback is a cost event, not just an availability event. If the
        # cheap model fails often enough, the saving is not what the table says
        # — and the table cannot know, because it was measured offline.
        by = {}
        for r in fb:
            by[r["model"]] = by.get(r["model"], 0) + 1
        paid = sum(r.get("cost_usd") or 0 for r in fb)
        print(f"\n{len(fb)} of {len(rows)} call(s) ({round(100*len(fb)/len(rows))}%) "
              f"fell back to a dearer model — ${paid:.5f} of the spend above.")
        for m, n in sorted(by.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>4}  {m}")
        print("  The saving in the routing table assumes the first choice answers.")
        print("  This is what it actually cost once it sometimes did not.")
    print("\n" + "-"*96)
    print("WHAT AGREEMENT CAN AND CANNOT TELL YOU — measured, 2026-08-21, 50 live samples")
    print("  routed model, agreement with the reference : 100%")
    print("  routed model, correct against ground truth :  75%")
    print("  They agreed and were BOTH WRONG on 12 of 50 — 24% of the traffic.")
    print("  So agreement detects DRIFT FROM THE REFERENCE and nothing else. It goes")
    print("  blind exactly where the two models share a blind spot, and a shared blind")
    print("  spot is the normal case, not the exotic one.")
    print("  **Shadow mode does not replace re-running the exam. It tells you WHEN to.**")
    print("-"*96)
    print(f"\nagreement with the reference: {agreed}/{n} ({round(100*agreed/n)}%), "
          f"interval {lo}-{hi}")
    if n < 30:
        print(f"That interval is {hi-lo} points wide. {n} samples cannot tell a healthy")
        print("router from a broken one — this is a sample count, not a verdict yet.")
    disagreed = [r for r in shadowed if r["agreed"] is False][:5]
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
