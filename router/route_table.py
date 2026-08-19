#!/usr/bin/env python3
"""
route_table.py — the routing decision, per sub-task, from measurement.

    python3 -m router.route_table

**The finding this file exists to act on.** A QA run is not one job. It is made
of *judging* steps ("is the button visible?") and *pointing* steps ("click the
button"). Both were measured on the same eight models, and the two abilities
turn out to be close to unrelated:

    google/gemma-3-12b-it   catches 83% of planted defects — best value by far
                            hits 11% of pointing targets — effectively blind
    mistralai/mistral-small catches 43%
                            hits 0/46. Not "poor". None.
    anthropic/claude-haiku  catches 77%, hits 70% — the only cheap model that
                            can do both

**So routing on "QA" as one task is how you build an agent that describes a
screen perfectly and clicks at random.** The task label has to be fine enough to
match the ability being bought, and how fine that is is a measurement, not a
taste. This is the concrete form of the no-hardcoding rule: the router routes
sub-tasks, because that is the grain at which models actually differ.

Each sub-task keeps its own reference and its own non-inferiority test, and the
answer is a table, not a model.
"""
import glob
import json
import os

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TASKS = {
    "qa-vision-assert": {
        "runs": os.path.join(CODE, "state", "runs"),
        "min_cases": 100,
        "what": "judge a screenshot against a statement (Midscene aiAssert)",
        "axes": [("catch", "catch_ci", "higher"), ("false_alarm", "false_alarm_ci", "lower")],
        "reference": "anthropic/claude-sonnet-5",
    },
    "text-faithful": {
        "runs": os.path.join(CODE, "state", "text_runs"),
        "min_cases": 60,
        "what": "is every claim in this text supported by its source",
        "axes": [("catch", "catch_ci", "higher"), ("false_alarm", "false_alarm_ci", "lower")],
        "reference": "anthropic/claude-sonnet-5",
    },
    "qa-vision-point": {
        "runs": os.path.join(CODE, "state", "point_runs"),
        "min_cases": 40,
        "what": "return the coordinates to click (Midscene aiTap / aiInput)",
        "axes": [("hit", "hit_ci", "higher"), ("wrong_thing", "wrong_thing_ci", "lower")],
        "reference": "anthropic/claude-sonnet-5",
    },
}


def latest(runs_dir, min_cases):
    best = {}
    for p in sorted(glob.glob(os.path.join(runs_dir, "*.json"))):
        s = json.load(open(p))["summary"]
        if s["cases"] >= min_cases:
            best[s["model"]] = s
    return list(best.values())


def survives(cand, ref, axes):
    """Not measurably worse than the reference on every axis. Intervals that do
    not meet are a real difference; intervals that overlap are not a proven one.
    """
    why = []
    for key, ci, better in axes:
        c, r = cand[ci], ref[ci]
        if better == "higher" and c[1] < r[0]:
            why.append(f"{key.replace('_',' ')} measurably lower "
                       f"({cand[key]}% vs {ref[key]}%)")
        if better == "lower" and c[0] > r[1]:
            why.append(f"{key.replace('_',' ')} measurably higher "
                       f"({cand[key]}% vs {ref[key]}%)")
    return why


def main():
    table = {}
    for task, cfg in TASKS.items():
        rows = latest(cfg["runs"], cfg["min_cases"])
        ref = next((r for r in rows if r["model"] == cfg["reference"]), None)
        if not ref:
            print(f"{task}: no reference run, skipped\n")
            continue
        ok = [r for r in rows if r["model"] != ref["model"]
              and not survives(r, ref, cfg["axes"])]
        ok.sort(key=lambda r: r["cost_usd"])
        pick = ok[0] if ok else ref
        table[task] = pick["model"]

        print(f"── {task} · {cfg['what']}")
        a1, a2 = cfg["axes"][0][0], cfg["axes"][1][0]
        print(f"   reference {ref['model']}: {a1} {ref[a1]}%, "
              f"{a2.replace('_',' ')} {ref[a2]}%, ${ref['cost_usd']:.5f}/run")
        # A golden set that cannot separate the pool is not measuring it. Say so
        # here rather than letting a wide interval read as a strong result.
        share = len(ok) / max(1, len(rows) - 1)
        if share >= 0.6:
            print(f"   ⚠ this set does not discriminate: {len(ok)} of "
                  f"{len(rows)-1} candidates survive the test. Whatever it picks "
                  f"is weakly\n     evidenced. Harden the set or add cases before "
                  f"trusting the choice.")
        print(f"   ROUTE TO  {pick['model']}  ${pick['cost_usd']:.5f}/run", end="")
        if pick["model"] != ref["model"]:
            print(f"  — {ref['cost_usd']/pick['cost_usd']:.0f}× cheaper, "
                  f"{a1} {pick[a1]}%, {a2.replace('_',' ')} {pick[a2]}%")
        else:
            print("  — nothing cheaper survives; the dear model is the answer")
        if len(ok) > 1:
            print(f"   also survive: {', '.join(r['model'] for r in ok[1:])}")
        print()

    if len(table) > 1:
        print("── the table")
        for t, m in table.items():
            print(f"   {t:<20} → {m}")
        if len(set(table.values())) > 1:
            print(f"\n   {len(set(table.values()))} different models across "
                  f"{len(table)} task types. There is no such thing as 'the cheap\n"
                  f"   model that still works' — only the cheap model that still works "
                  f"at THIS.")

    # The number the project is for: what a real mixed run costs, routed against
    # not routed. A QA run is mostly judging with some pointing; the mix is
    # stated rather than assumed, so a different mix can be checked.
    MIX = {"qa-vision-assert": 0.70, "qa-vision-point": 0.30}
    if all(t in table for t in MIX):
        base = routed = 0.0
        for task, share in MIX.items():
            cfg = TASKS[task]
            rows = latest(cfg["runs"], cfg["min_cases"])
            ref = next(r for r in rows if r["model"] == cfg["reference"])
            pick = next(r for r in rows if r["model"] == table[task])
            base += share * ref["cost_usd"] / ref["cases"]
            routed += share * pick["cost_usd"] / pick["cases"]
        print(f"── a 100-step QA run, {int(MIX['qa-vision-assert']*100)}% judging / "
              f"{int(MIX['qa-vision-point']*100)}% pointing")
        print(f"   all on the reference model : ${base*100:.4f}")
        print(f"   routed per sub-task        : ${routed*100:.4f}")
        print(f"   {base/routed:.0f}× cheaper, with no measurable quality loss on "
              f"either sub-task")

    # what a naive router would have done, and what it costs
    print("\n── what routing on one number would have picked")
    for task, cfg in TASKS.items():
        rows = latest(cfg["runs"], cfg["min_cases"])
        if not rows:
            continue
        key = "accuracy" if task == "qa-vision-assert" else "hit"
        best_cheap = min((r for r in rows if r.get(key, 0) >= 0.9 * max(x.get(key, 0) for x in rows)),
                         key=lambda r: r["cost_usd"], default=None)
        if best_cheap:
            print(f"   {task:<20} → {best_cheap['model']} "
                  f"(cheapest within 10% of the best {key})")


if __name__ == "__main__":
    main()
