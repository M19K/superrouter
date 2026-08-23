#!/usr/bin/env python3
"""
run_stability.py — how much of a score is the model, and how much is the dice?

**The question this answers.** The synthesis layer's `grounded` score moved
9, 8, 6, 7 across four runs of the same twelve questions in one day. Nobody
could say whether the system had changed or whether the local model simply
answers differently each time it is asked. A summary score cannot tell you:
two runs can both read 67% and disagree about a third of the cases.

So this compares two runs of the SAME exam CASE BY CASE and reports the
number that settles it — how many individual verdicts flipped.

    python3 run_stability.py <run-a.json> <run-b.json>
    python3 run_stability.py --latest 2 --model local/gpt-oss:20b

**Reading the output.** `agreement` is the ceiling on how precisely any single
run can be quoted. If two identical runs agree on 88% of cases, then a score
quoted to the nearest point is quoting noise, and the honest report is a range.

Run records come from SuperRouter's `superrouter.evals`, which stores one JSON
per run under `code/state/text_runs/`. Read-only: this never writes into that
project.
"""
import argparse
import glob
import os

from ._io import read_json

# Relative to this file, never to the author's home directory. This shipped
# pointing at `~/Documents/Mikoshi/...`, which exists on exactly one machine —
# the module was adopted from another project and its path came with it. On a
# stranger's clone it resolves to nothing and the tool reports no runs rather
# than an error, which is the quiet kind of wrong.
RUNS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "state", "text_runs")


def load(path):
    d = read_json(path)
    s = d.get("summary", {})
    by_id = {r["id"]: r for r in d.get("results", []) if "id" in r}
    return {"path": path, "summary": s, "by_id": by_id}


def compare(a, b):
    sa, sb = a["summary"], b["summary"]
    fa, fb = sa.get("golden_fingerprint"), sb.get("golden_fingerprint")
    if fa != fb:
        # The mistake SuperRouter already made once: comparing runs that sat
        # different versions of the same exam. Refuse rather than average.
        raise SystemExit(
            f"different case sets ({fa} vs {fb}) — these runs are not comparable")
    shared = sorted(set(a["by_id"]) & set(b["by_id"]))
    if not shared:
        raise SystemExit("no case ids in common")

    # A case with no verdict in one run is not a flip — it is a hole. Measured
    # 2026-08-22: 41 of 150 local cases came back with no answer, and every one
    # was a client TIMEOUT, not the model declining. Counting those as
    # disagreement would report the harness overloading a local server as model
    # non-determinism, which is the exact confusion this tool exists to end.
    holes = [i for i in shared
             if a["by_id"][i].get("said") is None or b["by_id"][i].get("said") is None]
    shared = [i for i in shared if i not in set(holes)]
    if not shared:
        raise SystemExit(f"every shared case is missing a verdict in one run "
                         f"({len(holes)} holes) — nothing to compare")

    same = [i for i in shared if a["by_id"][i]["said"] == b["by_id"][i]["said"]]
    flips = [i for i in shared if i not in set(same)]
    # A flip that changes the verdict's correctness is the one that moves a
    # score. A flip between two wrong answers does not.
    scoring = [i for i in flips
               if a["by_id"][i]["correct"] != b["by_id"][i]["correct"]]

    print(f"exam {fa} · {len(shared)} cases scored by both runs"
          + (f" · {len(holes)} excluded, no verdict in one run (check the "
             f"`error` field — a timeout is the harness, not the model)"
             if holes else "") + "\n")
    for r, tag in ((sa, "run A"), (sb, "run B")):
        print(f"  {tag}  accuracy {r.get('accuracy')}%  catch {r.get('catch')}%  "
              f"false alarms {r.get('false_alarm')}%  refused {r.get('refusal_pct')}%")
    print()
    print(f"  agreement   {len(same)}/{len(shared)} "
          f"({round(100 * len(same) / len(shared))}%) — same answer both times")
    print(f"  flipped     {len(flips)} cases answered differently")
    print(f"  score-moving {len(scoring)} of those changed right into wrong "
          f"or wrong into right")
    swing = abs(sa.get("accuracy", 0) - sb.get("accuracy", 0))
    # Two runs of one model measure noise. Two models measure difference. The
    # same arithmetic, and calling one the other is the whole point of the tool.
    same_model = sa.get("model") == sb.get("model")
    what = ("two runs of the same exam on the same model — this is NOISE"
            if same_model else
            f"{sa.get('model')} and {sb.get('model')} — this is a DIFFERENCE "
            f"between models, not run-to-run noise")
    print(f"\n  headline accuracy differs by {swing} points between {what}.")
    if same_model:
        print("  Quote a range, not a run." if swing else
              "  Identical headline — but check `flipped`: it can be non-zero anyway.")

    if flips:
        print("\n  flipped cases (id · truth · run A → run B):")
        for i in flips[:20]:
            ra, rb = a["by_id"][i], b["by_id"][i]
            print(f"    {i:<28} truth={str(ra['answer']):<5} "
                  f"{str(ra['said']):<5} → {str(rb['said']):<5}"
                  f"   [{ra.get('corruption') or 'healthy'}]")
        if len(flips) > 20:
            print(f"    … and {len(flips) - 20} more")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*", help="two run-record JSON files")
    ap.add_argument("--latest", type=int, metavar="N",
                    help="take the newest N run records instead")
    ap.add_argument("--model", default=None,
                    help="with --latest, only records for this model")
    a = ap.parse_args()

    paths = a.runs
    if a.latest:
        pat = os.path.join(RUNS, "*.json")
        cand = sorted(glob.glob(pat))
        if a.model:
            slug = a.model.replace("/", "_")
            cand = [p for p in cand if slug in os.path.basename(p)]
        paths = cand[-a.latest:]
    if len(paths) != 2:
        raise SystemExit("need exactly two run records; got "
                         f"{len(paths)}: {[os.path.basename(p) for p in paths]}")
    print(f"A: {os.path.basename(paths[0])}\nB: {os.path.basename(paths[1])}\n")
    compare(load(paths[0]), load(paths[1]))


if __name__ == "__main__":
    main()
