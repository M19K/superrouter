#!/usr/bin/env python3
"""
policy.py — turn measured quality into a routing decision, without inventing a
threshold.

**This is the part that is ours.** Every open-source router surveyed takes
quality as a number you declare: vLLM Semantic Router's own docs list
"quality scoring depends on quality_score being configured per model" under
Known Limitations. Research routing frameworks reduce quality to
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

    python3 -m superrouter.policy --min-catch 70 --max-false-alarm 15
    python3 -m superrouter.policy --min-catch 50 --max-false-alarm 25 --optimistic
"""
import argparse

import os

from .curve import latest_per_model

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_local(row):
    """A model served from this machine rather than bought per call."""
    return str(row.get("model", "")).startswith("local/")


def decide(rows, min_catch, max_fa, optimistic=False, max_seconds=None,
           include_local=True):
    """Return (qualified, rejected).

    `optimistic` scores on point estimates instead of bounds — offered only so
    the difference between the two is visible, because that difference is the
    argument.

    ── Two things a cost-and-quality answer gets wrong ────────────────────────

    **`max_seconds` — speed is a requirement, not a footnote.** Measured
    2026-08-28 on one 440-case exam: the winning model answered in 20.3 seconds
    a case and the runner-up in 0.7. Same task, same questions, **29x** apart —
    two and a half hours against five minutes. A table that ranks on price and
    quality alone hands you the first and calls it the best answer, which it is
    for an overnight batch and is useless behind anything a person waits on.
    So the caller states the ceiling, the same way it already states the quality
    bar, and a model that cannot meet it does not qualify.

    **`include_local` — a free model is not competing on price.** [@maaz ·
    2026-08-28] Routing exists to spend less money. A model hosted on your own
    machine costs nothing per call by definition, so it sorts first on every
    price comparison and wins by construction rather than by merit — which
    defeats the purpose of the comparison. It is still worth measuring, and on
    that same exam the local model was the most accurate thing on the board. But
    it belongs in a different question: *should this run locally at all*, decided
    once, against wall-clock and the machine it occupies. Not *which paid model
    is cheapest*, decided per task.
    """
    qualified, rejected = [], []
    for r in rows:
        c_lo, c_hi = r.get("catch_ci") or (0, 100)
        f_lo, f_hi = r.get("false_alarm_ci") or (0, 100)
        catch = r["catch"] if optimistic else c_lo
        fa = r["false_alarm"] if optimistic else f_hi
        secs = r.get("seconds_per_case")
        why = []
        if catch < min_catch:
            why.append(f"catch {catch}% < {min_catch}% required")
        if fa > max_fa:
            why.append(f"false alarms {fa}% > {max_fa}% allowed")
        if max_seconds is not None:
            if secs is None:
                # Unmeasured latency is not fast latency. Same rule as an
                # unproven catch rate: not shown is not qualified.
                why.append(f"latency never measured, and {max_seconds}s required")
            elif secs > max_seconds:
                why.append(f"{secs:.1f}s per call > {max_seconds}s allowed")
        if not include_local and not r.get("cost_usd"):
            # **"Paid" means it has a price, wherever it runs.** Excluding only
            # `local/` left a provider's free tier winning every price
            # comparison for exactly the same reason — zero divided into any
            # reference is not a saving, it is a different question. The two
            # are still distinguished in the reason, because they fail
            # differently: a local model costs you time and a machine, a free
            # hosted one costs you a rate limit and an endpoint that can be
            # withdrawn without notice.
            why.append("runs on this machine — free, so not competing on price"
                       if is_local(r) else
                       "free at the provider — no price to compare, and no "
                       "commitment that it stays free")
        (qualified if not why else rejected).append({**r, "_why": why,
                                                     "_catch_used": catch, "_fa_used": fa})
    # Paid models sort on price. A local model has no price, so sorting it
    # alongside them puts it first on a comparison it never entered; it is
    # listed after, and labelled.
    qualified.sort(key=lambda r: (is_local(r), r["cost_usd"]))
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
    ap.add_argument("--task", default="qa-vision-assert",
                    help="which task's records to read")
    ap.add_argument("--set", dest="set_name", default=None,
                    help="a named product exam, e.g. locus")
    ap.add_argument("--max-seconds", type=float, default=None, metavar="S",
                    help="latency ceiling per call. A model that has never been "
                         "timed does not qualify, the same as an unproven catch "
                         "rate — not shown is not qualified.")
    ap.add_argument("--paid-only", action="store_true",
                    help="exclude locally-hosted models. They cost nothing per "
                         "call, so they win every price comparison by "
                         "construction rather than by merit; whether to run "
                         "locally at all is a different question, decided once.")
    a = ap.parse_args()

    import os as _os
    _dirs = {"qa-vision-assert": "runs", "text-faithful": "text_runs",
             "qa-vision-point": "point_runs"}
    _d = _dirs.get(a.task, "runs") + (f"_{a.set_name}" if a.set_name else "")
    rows = latest_per_model(runs_dir=_os.path.join(CODE, "state", _d))

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
            print("           no measurable loss on either axis at 95% confidence")
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

    ok, no = decide(rows, a.min_catch, a.max_false_alarm, a.optimistic,
                    max_seconds=a.max_seconds, include_local=not a.paid_only)
    ref = next((r for r in rows if r["model"] == a.reference), None)

    if not ok:
        print("Nothing measured meets this requirement.")
        print("That is an answer: either the job needs the dear model, or the set")
        print("is not yet large enough to prove a cheap one. Do not relax it here.\n")
    else:
        pick = ok[0]
        secs = pick.get("seconds_per_case")
        print(f"ROUTE TO   {pick['model']}")
        print(f"           ${pick['cost_usd']:.5f} per run · catch {pick['catch']}% "
              f"(bound {pick['_catch_used']}%) · false alarms {pick['false_alarm']}% "
              f"(bound {pick['_fa_used']}%)"
              + (f" · {secs:.1f}s per call" if secs is not None else
                 " · latency never measured"))
        # **A free model has no ratio.** `reference_cost / 0` is a crash, and it
        # was reached the first time a free model won — which is the same fact
        # the sort order now encodes: a model that costs nothing is not
        # competing on price and cannot be expressed as a multiple of one.
        if ref and pick["model"] != ref["model"]:
            if not pick["cost_usd"]:
                where = "on your own machine" if is_local(pick) else "free at the provider"
                print(f"           costs nothing per call ({where}), so there is no "
                      f"saving multiple to quote against {ref['model']}")
                rs = ref.get("seconds_per_case")
                if secs is not None and rs:
                    # Say which direction it went. A ratio printed with a fixed
                    # word gets it backwards half the time, and "1x slower"
                    # while being faster is worse than saying nothing.
                    ratio = secs / rs
                    if ratio >= 1.15:
                        how = f"{ratio:.0f}× slower"
                    elif ratio <= 0.87:
                        how = f"{1 / ratio:.0f}× faster"
                    else:
                        how = "about the same speed"
                    print(f"           what it costs instead is time: {secs:.1f}s "
                          f"per call against {rs:.1f}s — {how}")
            elif ref["cost_usd"]:
                print(f"           {ref['cost_usd'] / pick['cost_usd']:.0f}× cheaper than "
                      f"{ref['model']}, on {pick['cases']} measured cases")
        if len(ok) > 1:
            print(f"\n           also qualified: "
                  f"{', '.join(r['model'] for r in ok[1:])}")

    print(f"\nrejected ({len(no)}):")
    for r in no:
        print(f"  {r['model']:<48} {'; '.join(r['_why'])}")

    if not a.optimistic:
        ok2, _ = decide(rows, a.min_catch, a.max_false_alarm, optimistic=True,
                        max_seconds=a.max_seconds, include_local=not a.paid_only)
        gained = [r["model"] for r in ok2 if r["model"] not in {q["model"] for q in ok}]
        if gained:
            print(f"\nJudging on point estimates instead would also have admitted: "
                  f"{', '.join(gained)}.")
            print("Those are not proven to meet the requirement — they are merely not "
                  "proven to miss it.")


if __name__ == "__main__":
    main()
