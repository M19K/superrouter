#!/usr/bin/env python3
"""
route_table.py — the routing decision, per sub-task, from measurement.

    python3 -m superrouter.route_table

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

# Measured across two products with the same generic generator, both task types.
#
#            rank correlation   median level shift
#   judging        0.83          −22 points (every model worse on product B)
#   pointing       0.94          +13 points (most models BETTER on product B)
#
# The first read of this was "models get worse on an unseen product". Pointing
# refutes that: the shift went the other way. The right reading is that the
# LEVEL is a property of the product, not of the model — a docs site with large
# obvious navigation is simply easier to point at than an unconventional layout.
#
# So: ORDER transfers well, and a published leaderboard is a fair guess at it.
# The LEVEL does not transfer in either direction, and the level is the only
# thing that answers "is this model good enough for me". That is why this ships
# as a method pointed at your product and never as a table of picks.
PRODUCTS = {
    "portfolio": os.path.join(CODE, "state", "runs_portfolio"),
    "midscene-docs": os.path.join(CODE, "state", "runs_midscene-docs"),
}

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


def same_exam(rows):
    """Keep only the runs that sat the newest exam, and say who is excluded.

    Comparing a model scored on an old golden set with one scored on a new one
    ranks the exams, not the models. On 2026-08-21 that put a free model scored
    on 90 easy cases above one scored on the 592-case redesign. Silence there is
    worse than an empty table: it produces a confident routing decision from a
    comparison that was never valid.
    """
    stamped = [r for r in rows if r.get("golden_fingerprint")]
    if not stamped:
        return rows, []
    newest = max(stamped, key=lambda r: r.get("_run_file") or "")["golden_fingerprint"]
    keep = [r for r in rows if r.get("golden_fingerprint") == newest]
    stale = [r for r in rows if r.get("golden_fingerprint") != newest]
    if stale:
        print(f"   ({len(stale)} model(s) excluded — last measured on an older "
              f"version of this exam, so their scores are not comparable: "
              f"{', '.join(sorted({r['model'].split('/')[-1] for r in stale})[:4])}"
              f"{' …' if len({r['model'] for r in stale}) > 4 else ''})")
    return keep, stale


def latest(runs_dir, min_cases):
    """Newest run per model. Run files are named with their timestamp, so
    sorted order is chronological — and that order is carried through, because
    which exam is current can only be decided by when it was sat."""
    best = {}
    for p in sorted(glob.glob(os.path.join(runs_dir, "*.json"))):
        s = json.load(open(p))["summary"]
        if s["cases"] >= min_cases:
            s = dict(s, _run_file=os.path.basename(p))
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


def per_product():
    """One table per product, because a bar set on one is meaningless on another."""
    base = TASKS["qa-vision-assert"]
    out = {}
    for name, runs in PRODUCTS.items():
        rows, _stale = same_exam(latest(runs, 100))
        ref = next((r for r in rows if r["model"] == base["reference"]), None)
        if not ref:
            continue
        ok = [r for r in rows if r["model"] != ref["model"]
              and not survives(r, ref, base["axes"])]
        ok.sort(key=lambda r: r["cost_usd"])
        pick = ok[0] if ok else ref
        out[name] = (ref, pick, rows)
    return out


def main():
    products = per_product()
    if len(products) > 1:
        print("── the same task, measured on two products\n")
        names = list(products)
        allm = sorted({r["model"] for _, _, rows in products.values() for r in rows},
                      key=lambda m: -next((r["catch"] for r in products[names[0]][2]
                                           if r["model"] == m), 0))
        print(f"   {'model':<42} " + " ".join(f"{n[:12]:>12}" for n in names) + "   drop")
        for m in allm:
            vals = []
            for n in names:
                r = next((x for x in products[n][2] if x["model"] == m), None)
                vals.append(r["catch"] if r else None)
            if any(v is None for v in vals):
                continue
            print(f"   {m:<42} " + " ".join(f"{v:>11}%" for v in vals) +
                  f"   {vals[0]-vals[1]:>4}")
        for n in names:
            ref, pick, _ = products[n]
            print(f"\n   {n:<14} routes to {pick['model']}"
                  f"  (${pick['cost_usd']:.5f}/run, catch {pick['catch']}%)")
        print("\n   Different products, different picks and different absolute levels."
              "\n   A table published for one product does not describe another — "
              "measure yours.\n")

    table = {}
    for task, cfg in TASKS.items():
        rows, _stale = same_exam(latest(cfg["runs"], cfg["min_cases"]))
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
        # Many survivors mean one of two very different things, and calling both
        # a warning would be wrong. Either several models are genuinely tied at
        # the top — in which case picking the cheapest is exactly right and the
        # evidence is strong — or the set is too small or too easy to tell them
        # apart, in which case the pick is weakly evidenced. The difference is
        # whether the survivors actually score well, not how many there are.
        share = len(ok) / max(1, len(rows) - 1)
        if share >= 0.6:
            a1 = cfg["axes"][0][0]
            tied = [r for r in ok if r[a1] >= ref[a1] - 3]
            widest = max((r[cfg["axes"][0][1]][1] - r[cfg["axes"][0][1]][0])
                         for r in ok)
            if len(tied) >= 2 and widest <= 25:
                print(f"   ✓ {len(tied)} models are genuinely tied with the "
                      f"reference on {a1}. Picking the cheapest is the\n"
                      f"     right call and the evidence is strong — this is what "
                      f"routing is for.")
            else:
                print(f"   ⚠ this set does not discriminate: {len(ok)} of "
                      f"{len(rows)-1} candidates survive and the intervals are "
                      f"{widest} points wide.\n     Whatever it picks is weakly "
                      f"evidenced. Harden the set or add cases first.")
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
            rows, _stale = same_exam(latest(cfg["runs"], cfg["min_cases"]))
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
        rows, _stale = same_exam(latest(cfg["runs"], cfg["min_cases"]))
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
