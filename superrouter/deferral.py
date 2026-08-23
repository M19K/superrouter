#!/usr/bin/env python3
"""
deferral.py — is the router better than a coin flip at the same routing rate?

    python3 -m superrouter.deferral --task text-faithful
    python3 -m superrouter.deferral --task text-faithful --cheap google/gemma-3-12b-it

**The criticism this answers, and it is the sharpest one aimed at this project.**

A saving percentage is a property of your escalation threshold, not of your
router. If the cheap tier costs nothing and you send fraction *f* upstairs, the
saving is `1 − f` by arithmetic. **A router that picks by coin flip reports
exactly the same saving as one that picks brilliantly.** So "60× cheaper" or
"3.6× cheaper", on its own, says nothing about whether the routing was any good.

The honest question is not *how much did I save*. It is:

    at the same routing rate, is my router more accurate than random?

So this plots accuracy against routing rate between three reference points, on
the same cases:

    all-cheap     never escalate                  the floor
    random @ f    escalate a random fraction f    the line a router must beat
    oracle @ f    escalate exactly what the cheap  the ceiling nobody reaches
                  tier got wrong

A router with real judgement **bows above the random line**. The gap between the
two is what the routing actually bought; everything else was arithmetic.

**This costs nothing to run.** Every scored run already records, per case, which
models were right — so the whole cascade is replayed from the records rather
than re-executed. Nothing is called and no money is spent.

Credit: the framing, the three reference points and the strictness ladder in
`cascade.py` are taken from a published walkthrough of an SLM+LLM cascade
router. The criticism was aimed at claims like ours and it was correct.
"""
import argparse
import glob
import json
import os
import random
import statistics

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DIRS = {"text-faithful": "text_runs",
        "qa-vision-assert": "runs_portfolio",
        "qa-vision-point": "point_runs_portfolio"}


def load(task):
    """Every model's per-case verdict, keyed by case id, for one exam."""
    d = os.path.join(CODE, "state", DIRS[task])
    runs = {}
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        b = json.load(open(p))
        s = b["summary"]
        if s.get("cases", 0) < 40:
            continue
        runs[s["model"]] = b
    if not runs:
        return {}, None
    # only models that sat the same exam may be compared — the rule the rest of
    # this project already enforces, and it matters more here because a cascade
    # mixes two models' verdicts on the same case
    exams = {}
    for m, b in runs.items():
        exams.setdefault(b["summary"].get("exam_fingerprint"), []).append(m)
    exam = max(exams, key=lambda k: len(exams[k]))
    keep = set(exams[exam])
    out = {}
    for m in keep:
        rows = {r["id"]: r for r in runs[m]["results"]}
        out[m] = {"rows": rows, "summary": runs[m]["summary"]}
    return out, exam


def common_cases(models, only=None):
    """Cases both tiers actually sat.

    Intersecting across EVERY model returns nothing: runs use different
    `--limit` sizes, so an eight-model intersection is empty while any given
    pair overlaps almost completely. A cascade is two tiers, so the pair is the
    right unit — and comparing a cascade on cases one tier never saw would be
    the same error as comparing two different exams.
    """
    names = only or list(models)
    ids = None
    for m in names:
        s = set(models[m]["rows"])
        ids = s if ids is None else (ids & s)
    return sorted(ids or [])


def correct(row):
    return bool(row.get("correct")) if "correct" in row else row.get("outcome") == "hit"


def curve(cheap_rows, ref_rows, ids, seed=7, points=11):
    """Accuracy against routing rate, for the three reference strategies."""
    rnd = random.Random(seed)
    wrong = [i for i in ids if not correct(cheap_rows[i])]
    n = len(ids)
    base = sum(correct(cheap_rows[i]) for i in ids) / n
    ceiling = sum(correct(ref_rows[i]) for i in ids) / n

    out = []
    for k in range(points):
        f = k / (points - 1)
        take = int(round(f * n))

        # ORACLE: spend the budget only on cases the cheap tier got wrong
        orc = set(wrong[:take])
        acc_o = sum(correct(ref_rows[i]) if i in orc else correct(cheap_rows[i])
                    for i in ids) / n

        # RANDOM: spend the same budget on cases chosen blindly. Averaged over
        # many draws, because a single draw is itself noise.
        accs = []
        for _ in range(60):
            pick = set(rnd.sample(ids, take)) if take else set()
            accs.append(sum(correct(ref_rows[i]) if i in pick else correct(cheap_rows[i])
                            for i in ids) / n)
        out.append({"rate": f, "random": statistics.mean(accs),
                    "oracle": acc_o, "n_escalated": take})
    return out, base, ceiling


def sparkline(rows, key, lo, hi):
    blocks = "▁▂▃▄▅▆▇█"
    return "".join(blocks[min(7, max(0, int((r[key] - lo) / (hi - lo + 1e-9) * 7.99)))]
                   for r in rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="text-faithful", choices=sorted(DIRS))
    ap.add_argument("--cheap")
    ap.add_argument("--reference")
    a = ap.parse_args()

    models, exam = load(a.task)
    if len(models) < 2:
        raise SystemExit(f"need two models on one exam for {a.task}; found {len(models)}")
    ranked = sorted(models, key=lambda m: models[m]["summary"].get("cost_usd", 0))
    cheap = a.cheap or ranked[0]
    ref = a.reference or ranked[-1]
    if cheap not in models or ref not in models:
        raise SystemExit(f"available: {', '.join(ranked)}")
    ids = common_cases(models, only=[cheap, ref])
    if len(ids) < 30:
        raise SystemExit(f"{cheap} and {ref} share only {len(ids)} cases — "
                         f"too few to draw a curve on")

    cr, rr = models[cheap]["rows"], models[ref]["rows"]
    rows, base, ceiling = curve(cr, rr, ids)

    print(f"deferral curve · {a.task} · exam {exam} · {len(ids)} cases replayed "
          f"from records, nothing spent\n")
    print(f"  cheap tier   {cheap:<44} accuracy {base:.3f}")
    print(f"  reference    {ref:<44} accuracy {ceiling:.3f}\n")
    print(f"{'rate':>6} {'escalated':>10} {'random':>9} {'oracle':>9} {'headroom':>10}")
    print("-" * 50)
    for r in rows:
        print(f"{r['rate']:>6.1f} {r['n_escalated']:>10} {r['random']:>9.3f} "
              f"{r['oracle']:>9.3f} {r['oracle']-r['random']:>10.3f}")

    lo = min(min(r["random"], r["oracle"]) for r in rows)
    hi = max(max(r["random"], r["oracle"]) for r in rows)
    print(f"\n  random  {sparkline(rows,'random',lo,hi)}")
    print(f"  oracle  {sparkline(rows,'oracle',lo,hi)}")

    mid = rows[len(rows) // 2]
    print(f"\n  At a 50% routing rate: random reaches {mid['random']:.3f}, "
          f"a perfect chooser {mid['oracle']:.3f}.")
    print(f"  **That {mid['oracle']-mid['random']:.3f} gap is the entire prize.** A router "
          f"is worth having\n  only in so far as it lands inside it — and a saving figure "
          f"cannot tell you\n  whether it did, because random saves exactly as much.")
    print(f"\n  Nothing here is a claim about SuperRouter yet. It is the measuring stick")
    print(f"  that any escalation policy has to be held against. `cascade.py` puts a")
    print(f"  real verifier on this axis.")


if __name__ == "__main__":
    main()
