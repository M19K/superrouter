#!/usr/bin/env python3
"""
policy.py — turn measured quality into a routing decision, without inventing a
threshold.

**This is the part that is ours.** Every open-source router surveyed takes
quality as a number you declare: vLLM Semantic Router's own docs list
"quality scoring depends on quality_score being configured per model" under
Known Limitations. LLMRouter, the strongest base to build on, reduces quality to
a single float per (query, model). Both are reasonable engineering. Neither can
tell you what you lose by going cheaper, because neither ever measured it.

Three things this does differently:

**1 · Quality is resolved by failure mode, not collapsed to one number.**
A single score cannot separate a model that misses defects from one that invents
them. Measured here: on the QA-vision task, `gemma-3-12b-it` and
`gemini-2.5-flash-lite` sit 4 points apart on accuracy and are opposites — one
catches 83% of defects and cries wolf on 21% of healthy screens, the other
catches 58% and cries wolf on 9%. Accuracy calls them equivalent. They are not
interchangeable for any real job.

**2 · The caller states the requirement; the policy does not guess one.**
"Cheapest model that holds quality" needs a definition of *holds*. Rather than
pick a global threshold — which would be exactly the guess this project exists
to prevent — a caller says what its job needs: "catch at least 70% of defects,
raise false alarms on at most 15% of healthy screens." The policy returns the
cheapest model that meets it. A different job asks for something different.

**3 · Decisions are made on the confidence bound, never the point estimate.**
This is the one that changes answers. Scored on 6 defect cases,
`llama-4-scout` caught 6/6 and looked like the outright winner. Scored on 53, it
catches 55% and is second-from-bottom. The point estimate flipped; the interval
had said all along that 6 cases could not tell these models apart. So a model
qualifies only when the **lower** bound of its measured catch rate clears the
requirement, and the **upper** bound of its false-alarm rate stays under the
ceiling. Being unproven is treated as not qualifying.

    python3 -m router.policy --min-catch 70 --max-false-alarm 15
    python3 -m router.policy --min-catch 50 --max-false-alarm 25 --optimistic
"""
import argparse
import json

from .curve import latest_per_model


def decide(rows, min_catch, max_fa, optimistic=False):
    """Return (qualified, rejected). `optimistic` scores on point estimates
    instead of bounds — offered only so the difference between the two is
    visible, because that difference is the argument."""
    qualified, rejected = [], []
    for r in rows:
        c_lo, c_hi = r.get("catch_ci") or (0, 100)
        f_lo, f_hi = r.get("false_alarm_ci") or (0, 100)
        catch = r["catch"] if optimistic else c_lo
        fa = r["false_alarm"] if optimistic else f_hi
        why = []
        if catch < min_catch:
            why.append(f"catch {catch}% < {min_catch}% required")
        if fa > max_fa:
            why.append(f"false alarms {fa}% > {max_fa}% allowed")
        (qualified if not why else rejected).append({**r, "_why": why,
                                                     "_catch_used": catch, "_fa_used": fa})
    qualified.sort(key=lambda r: r["cost_usd"])
    rejected.sort(key=lambda r: -r["catch"])
    return qualified, rejected


def not_worse_than(rows, reference):
    """The question a business actually asks: **give me the cheapest model that
    is not measurably worse than the one I trust.**

    That is a non-inferiority test, and it is the honest form of "holds quality"
    — you can never prove two models are equal, only fail to show a difference.
    A candidate survives when its measured interval overlaps the reference's on
    both axes: we cannot demonstrate it is worse at catching defects, and we
    cannot demonstrate it raises more false alarms.

    It is deliberately not symmetric with `decide()`. An absolute requirement
    asks "is this good enough in itself"; this asks "would swapping cost me
    anything I can measure". Cost questions are the second kind.
    """
    ref = next((r for r in rows if r["model"] == reference), None)
    if not ref:
        return None, [], []
    rc, rf = ref["catch_ci"], ref["false_alarm_ci"]
    ok, no = [], []
    for r in rows:
        if r["model"] == reference:
            continue
        c, f = r["catch_ci"], r["false_alarm_ci"]
        why = []
        if c[1] < rc[0]:
            why.append(f"catches measurably less ({r['catch']}% vs {ref['catch']}%, "
                       f"intervals {c[0]}-{c[1]} vs {rc[0]}-{rc[1]} do not meet)")
        if f[0] > rf[1]:
            why.append(f"measurably more false alarms ({r['false_alarm']}% vs "
                       f"{ref['false_alarm']}%, {f[0]}-{f[1]} vs {rf[0]}-{rf[1]})")
        (ok if not why else no).append({**r, "_why": why})
    ok.sort(key=lambda r: r["cost_usd"])
    return ref, ok, no


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--like", metavar="MODEL",
                    help="cheapest model not measurably worse than MODEL")
    ap.add_argument("--min-catch", type=float,
                    help="percent of planted defects the job needs caught")
    ap.add_argument("--max-false-alarm", type=float,
                    help="percent of healthy screens it may wrongly flag")
    ap.add_argument("--optimistic", action="store_true",
                    help="judge on point estimates instead of confidence bounds")
    ap.add_argument("--reference", default="anthropic/claude-sonnet-5")
    a = ap.parse_args()

    rows = latest_per_model()

    if a.like:
        ref, ok, no = not_worse_than(rows, a.like)
        if not ref:
            raise SystemExit(f"no scored run for {a.like}")
        print(f"cheapest model not measurably worse than {ref['model']}")
        print(f"reference · catch {ref['catch']}% ({ref['catch_ci'][0]}-{ref['catch_ci'][1]})"
              f" · false alarms {ref['false_alarm']}% "
              f"({ref['false_alarm_ci'][0]}-{ref['false_alarm_ci'][1]})"
              f" · ${ref['cost_usd']:.5f} per run · {ref['cases']} cases\n")
        if ok:
            p = ok[0]
            print(f"ROUTE TO   {p['model']}")
            print(f"           ${p['cost_usd']:.5f} per run — "
                  f"{ref['cost_usd'] / p['cost_usd']:.0f}× cheaper")
            print(f"           catch {p['catch']}% "
                  f"({p['catch_ci'][0]}-{p['catch_ci'][1]}) · false alarms "
                  f"{p['false_alarm']}% ({p['false_alarm_ci'][0]}-{p['false_alarm_ci'][1]})")
            print(f"           no measurable loss on either axis at 95% confidence")
            if len(ok) > 1:
                print(f"\n           also survive: {', '.join(r['model'] for r in ok[1:])}")
        else:
            print("Every candidate is measurably worse. The dear model is the answer.")
        print(f"\nruled out ({len(no)}):")
        for r in no:
            print(f"  {r['model']:<44} {'; '.join(r['_why'])}")
        return

    if a.min_catch is None or a.max_false_alarm is None:
        raise SystemExit("give --like MODEL, or both --min-catch and --max-false-alarm")
    basis = "point estimates (optimistic)" if a.optimistic else "confidence bounds"
    print(f"requirement · catch ≥ {a.min_catch:g}% · false alarms ≤ {a.max_false_alarm:g}%"
          f"   judged on {basis}\n")

    ok, no = decide(rows, a.min_catch, a.max_false_alarm, a.optimistic)
    ref = next((r for r in rows if r["model"] == a.reference), None)

    if not ok:
        print("Nothing measured meets this requirement.")
        print("That is an answer: either the job needs the dear model, or the set")
        print("is not yet large enough to prove a cheap one. Do not relax it here.\n")
    else:
        pick = ok[0]
        print(f"ROUTE TO   {pick['model']}")
        print(f"           ${pick['cost_usd']:.5f} per run · catch {pick['catch']}% "
              f"(bound {pick['_catch_used']}%) · false alarms {pick['false_alarm']}% "
              f"(bound {pick['_fa_used']}%)")
        if ref and ref["cost_usd"] and pick["model"] != ref["model"]:
            print(f"           {ref['cost_usd'] / pick['cost_usd']:.0f}× cheaper than "
                  f"{ref['model']}, on {pick['cases']} measured cases")
        if len(ok) > 1:
            print(f"\n           also qualified: "
                  f"{', '.join(r['model'] for r in ok[1:])}")

    print(f"\nrejected ({len(no)}):")
    for r in no:
        print(f"  {r['model']:<48} {'; '.join(r['_why'])}")

    if not a.optimistic:
        ok2, _ = decide(rows, a.min_catch, a.max_false_alarm, optimistic=True)
        gained = [r["model"] for r in ok2 if r["model"] not in {q["model"] for q in ok}]
        if gained:
            print(f"\nJudging on point estimates instead would also have admitted: "
                  f"{', '.join(gained)}.")
            print("Those are not proven to meet the requirement — they are merely not "
                  "proven to miss it.")


if __name__ == "__main__":
    main()
