#!/usr/bin/env python3
"""
cascade.py — try the cheap model first, and escalate only when it looks unsure.

    python3 -m superrouter.cascade --task text-faithful --measure     # live, costs cents
    python3 -m superrouter.cascade --task text-faithful               # replay, free

**A different question from the rest of this project.** Everywhere else,
SuperRouter picks a model *before* the work, from what that model scored on your
exam. A cascade picks *after*: the cheap tier answers, something inspects the
answer, and only the doubtful ones go upstairs. The two compose — the cascade's
tiers are chosen by the measurement, and the measurement is what tells you
whether the cascade is worth running at all.

── The bar it has to clear ──────────────────────────────────────────────────

Not "how much did it save". A cascade that escalates a random tenth of traffic
saves exactly what one that escalates the *right* tenth saves — the saving is
arithmetic on the escalation rate and says nothing about judgement. The only
question worth asking is the one `deferral.py` draws:

    at the same escalation rate, is this more accurate than escalating at random?

So every strictness level below is placed on that curve, against random at its
own rate. A level that sits on the random line is a level that bought nothing,
however much it saved.

── The strictness ladder ────────────────────────────────────────────────────

Monotonic on purpose: each level escalates a superset of the level below, so
the escalation rate cannot fall as strictness rises. That is what makes the
levels a clean left-to-right series rather than a scatter.

    0  never escalate                                    the floor
    1  + no answer at all — empty, unparseable, errored
    2  + the answer breaks the format the prompt demanded
    3  + the answer hedges ("I'm not sure", "possibly")
    4  + K samples at temperature disagree with each other
    5  always escalate                                   the ceiling

**Levels 1–3 read the answer's shape, and on a one-word task there is almost no
shape to read** — measured here, they fire on under 1% of cases. They are built
because they cost nothing and they matter on free-text work; they are simply not
the interesting signal for a verdict task. **Level 4 is.**

── The cost rule that cascades get wrong ────────────────────────────────────

**An escalated query is paid for twice.** The cheap tier's tokens were already
spent when the decision to escalate was made. Any ledger that charges only the
expensive call understates a cascade, and understates it exactly in proportion
to how often it escalates — which is the number under examination. This module
charges both, always.
"""
import argparse
import glob
import json
import os
import random
import re
import statistics
import time

from .evals import ask, key, wilson

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = {"text-faithful": "text_runs", "qa-vision-assert": "runs_portfolio"}

HEDGES = re.compile(r"\b(not sure|unsure|unclear|possibly|perhaps|might be|i think|"
                    r"cannot determine|hard to say|ambiguous|it depends)\b", re.I)
CLEAN_VERDICT = re.compile(r"^\s*(TRUE|FALSE)\s*\.?\s*$", re.I)


def signals(row):
    """What the verifier can see, from one stored answer. No model is called."""
    raw = (row.get("raw") or "")
    return {
        1: bool(row.get("error")) or row.get("said") is None or not raw.strip(),
        2: not CLEAN_VERDICT.match(raw),
        3: bool(HEDGES.search(raw)),
    }


def load(task, model=None):
    d = os.path.join(CODE, "state", DIRS[task])
    runs = {}
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        b = json.load(open(p))
        if b["summary"].get("cases", 0) >= 40:
            runs[b["summary"]["model"]] = b
    exams = {}
    for m, b in runs.items():
        exams.setdefault(b["summary"].get("exam_fingerprint"), []).append(m)
    exam = max(exams, key=lambda k: len(exams[k]))
    return {m: runs[m] for m in exams[exam]}, exam


def correct(r):
    return bool(r.get("correct"))


def evaluate(cheap_rows, ref_rows, ids, escalate, cheap_cost, ref_cost):
    """Accuracy and true cost of one escalation policy, with double payment."""
    esc = [i for i in ids if escalate(cheap_rows[i])]
    acc = sum(correct(ref_rows[i]) if i in set(esc) else correct(cheap_rows[i])
              for i in ids) / len(ids)
    # every case pays the cheap tier; escalated cases pay the reference as well
    cost = len(ids) * cheap_cost + len(esc) * ref_cost
    return {"rate": len(esc) / len(ids), "accuracy": acc, "cost": cost,
            "escalated": len(esc)}


def random_at(cheap_rows, ref_rows, ids, rate, draws=200, seed=7):
    rnd = random.Random(seed)
    take = int(round(rate * len(ids)))
    out = []
    for _ in range(draws):
        pick = set(rnd.sample(ids, take)) if take else set()
        out.append(sum(correct(ref_rows[i]) if i in pick else correct(cheap_rows[i])
                       for i in ids) / len(ids))
    return statistics.mean(out), statistics.pstdev(out)


def measure_consistency(task, model, ids, cases, k, api_key, temp=0.7, workers=6):
    """Sample the cheap model K times at temperature; disagreement is the signal.

    This is the one verifier signal a one-word task gives you, and it is the
    only part of the ladder that costs anything — K extra cheap calls per case,
    and the cheap tier is cheap by construction.
    """
    import concurrent.futures as fu
    by_id = {c["id"]: c for c in cases}
    out, spent = {}, 0.0

    def one(i):
        votes, cost = [], 0.0
        for _ in range(k):
            try:
                r = ask(model, by_id[i]["assert"], None, api_key, temperature=temp)
            except Exception:
                return i, None, cost
            votes.append((r["text"] or "").strip().upper()[:5])
            cost += r["cost"]
        return i, votes, cost

    with fu.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, votes, cost in ex.map(one, ids):
            spent += cost
            out[i] = (len(set(votes)) > 1) if votes else True
    return out, spent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="text-faithful", choices=sorted(DIRS))
    ap.add_argument("--cheap")
    ap.add_argument("--reference")
    ap.add_argument("--measure", action="store_true",
                    help="run the level-4 self-consistency probe live (costs cents)")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--limit", type=int, default=120)
    a = ap.parse_args()

    runs, exam = load(a.task)
    ranked = sorted(runs, key=lambda m: runs[m]["summary"].get("cost_usd", 0))
    ref = a.reference or ranked[-1]
    rr = {r["id"]: r for r in runs[ref]["results"]}
    if a.cheap:
        cheap = a.cheap
    else:
        # cheapest tier that actually SHARES cases with the reference. Cost order
        # alone picked a run that sat a different subset, and an empty overlap
        # reads as a broken tool rather than as an incomparable pair.
        usable = [m for m in ranked if m != ref
                  and len(set(r["id"] for r in runs[m]["results"]) & set(rr)) >= 30]
        if not usable:
            raise SystemExit(f"no model shares 30+ cases with {ref}")
        cheap = usable[0]
    cr = {r["id"]: r for r in runs[cheap]["results"]}
    ids = sorted(set(cr) & set(rr))[: a.limit]
    if len(ids) < 30:
        raise SystemExit(f"{cheap} and {ref} share only {len(ids)} cases")

    n_cheap = runs[cheap]["summary"]["cases"] or 1
    n_ref = runs[ref]["summary"]["cases"] or 1
    c_cheap = runs[cheap]["summary"].get("cost_usd", 0) / n_cheap
    c_ref = runs[ref]["summary"].get("cost_usd", 0) / n_ref

    print(f"cascade · {a.task} · exam {exam} · {len(ids)} cases")
    print(f"  cheap tier   {cheap:<44} ${c_cheap:.6f}/call")
    print(f"  reference    {ref:<44} ${c_ref:.6f}/call\n")

    consistency, probe_cost = {}, 0.0
    if a.measure:
        cases = json.load(open(os.path.join(
            CODE, "golden", "text-faithful" if a.task == "text-faithful" else "qa-vision",
            "manifest.json")))["case_list"]
        print(f"  probing self-consistency: {a.k} samples × {len(ids)} cases on {cheap} …")
        consistency, probe_cost = measure_consistency(
            a.task, cheap, ids, cases, a.k, key())
        fired = sum(1 for v in consistency.values() if v)
        print(f"  disagreed on {fired}/{len(ids)} cases · probe cost ${probe_cost:.5f}\n")

    LEVELS = {
        0: ("never escalate", lambda r: False),
        1: ("+ no answer at all", lambda r: signals(r)[1]),
        2: ("+ breaks the format", lambda r: signals(r)[1] or signals(r)[2]),
        3: ("+ hedges", lambda r: any(signals(r).values())),
        5: ("always escalate", lambda r: True),
    }
    if consistency:
        LEVELS[4] = ("+ samples disagree",
                     lambda r: any(signals(r).values()) or consistency.get(r["id"], False))

    print(f"{'lvl':>4} {'what it adds':<24} {'rate':>6} {'accuracy':>9} "
          f"{'random':>8} {'gap':>7} {'cost':>9}")
    print("-" * 76)
    rows = []
    for lvl in sorted(LEVELS):
        label, fn = LEVELS[lvl]
        res = evaluate(cr, rr, ids, fn, c_cheap, c_ref)
        rnd_acc, rnd_sd = random_at(cr, rr, ids, res["rate"])
        gap = res["accuracy"] - rnd_acc
        beats = abs(gap) > 2 * rnd_sd if rnd_sd else False
        rows.append((lvl, res, gap, beats))
        mark = "  ←" if beats and gap > 0 else ""
        print(f"{lvl:>4} {label:<24} {res['rate']:>6.2f} {res['accuracy']:>9.3f} "
              f"{rnd_acc:>8.3f} {gap:>+7.3f} {res['cost']:>9.5f}{mark}")

    if a.measure and probe_cost:
        print(f"\n  The level-4 probe itself cost ${probe_cost:.5f} across {len(ids)} "
              f"cases (${probe_cost/len(ids):.6f}/call).")
        print(f"  **That is part of the price of the policy, not a free signal.** A "
              f"cascade\n  whose verifier costs more than it saves is a slower way to "
              f"spend the same money.")

    real = [r for r in rows if r[3] and r[2] > 0 and 0 < r[1]["rate"] < 1]
    print()
    if real:
        lvl, res, gap, _ = max(real, key=lambda t: t[2])
        print(f"  Level {lvl} beats random at its own rate by {gap:+.3f}, which is "
              f"outside the noise\n  of the random draw. That gap — not the saving — is "
              f"what the verifier bought.")
    else:
        print(f"  **No strictness level beats random at its own rate by more than the "
              f"noise.**\n  On this task the verifier is not earning its place: the "
              f"saving it reports is\n  arithmetic on the escalation rate, and a coin "
              f"flip would report the same.\n  Reported because it is the answer, not "
              f"because it is the one we wanted.")


if __name__ == "__main__":
    main()
