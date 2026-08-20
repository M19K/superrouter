#!/usr/bin/env python3
"""
llmrouter_bridge.py — build a complete LLMRouter training corpus from our runs,
and fix two things about their supervision signal on the way.

    python3 -m superrouter.llmrouter_bridge --label cost-aware
    python3 -m superrouter.llmrouter_bridge --label raw          # theirs, for comparison

LLMRouter (ulab-uiuc, MIT) is the routing engine — 16 algorithms, a training CLI,
a benchmark. This produces the files it loads: query data, query embeddings, the
routing table, and the LLM metadata. Nothing of theirs is vendored.

── Two fixes, both read out of their source rather than assumed ──────────────

**1 · The query text has to identify the whole input, including the image.**

`KNNRouter.__init__` selects labels with:

    routing_data_train.loc[routing_data_train.groupby("query")["performance"].idxmax()]

It groups by the **query string**. For a vision task the same sentence is asked
of many different screenshots — "every label is legible" is true of one frame and
false of another — so grouping by text alone merges cases whose right answers
differ. Measured on our corpus: 140 cases collapse to **25** groups, and the
labels for 115 of them are discarded.

So the query carries its frame: `[hub-dark] every section label is legible…`.
The general rule, and it is not specific to us — **for any multimodal routing
corpus the textual query must name the non-text input, or the label collapses.**

**2 · `argmax` over a binary score is not a routing objective.**

With `performance ∈ {0,1}`, most queries have several models tied at 1.0 —
measured here, **every** query does. `idxmax` breaks that tie by row order, so
the router is trained to predict whichever correct model happened to be written
first. Cost never enters. A router trained that way cannot save money except by
accident, which is the accident this project exists to remove.

The fix is in the label, not the algorithm: keep correctness dominant, then rank
the correct models by what they cost.

    performance = 0                       if wrong
                = 1 − 0.5 · cost_rank     if right   (cheapest correct → 1.0)

Correctness still outranks price — every right answer scores above every wrong
one — so nothing is traded away. But `argmax` now means *the cheapest model that
got it right*, which is the thing we actually want routed to. **Their 16
algorithms are unchanged and train toward a better target.**
"""
import argparse
import glob
import json
import math
import os
import random

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(CODE, "state", "runs")
POINT = os.path.join(CODE, "state", "point_runs")
OUT = os.path.join(CODE, "state", "llmrouter_corpus")


def newest(runs_dir, min_cases):
    best = {}
    for p in sorted(glob.glob(os.path.join(runs_dir, "*.json"))):
        b = json.load(open(p))
        if b["summary"]["cases"] >= min_cases:
            best[b["summary"]["model"]] = b
    return best


def collect():
    """One record per (case, model), across both task types."""
    recs = []
    for blob in newest(RUNS, 100).values():
        m = blob["summary"]["model"]
        for r in blob["results"]:
            recs.append({"task": "qa_vision_assert", "case": r["id"], "frame": r["frame"],
                         "text": r["assert"], "truth": "TRUE" if r["answer"] else "FALSE",
                         "model": m, "response": r.get("raw", ""),
                         "correct": bool(r["correct"]), "cost": r["cost"],
                         "seconds": r["seconds"],
                         "tokens": (r.get("in_tokens") or 0) + (r.get("out_tokens") or 0),
                         "defect": r.get("defect"),
                         "needs_defect_sight": r["needs_defect_sight"]})
    for blob in newest(POINT, 100).values():
        m = blob["summary"]["model"]
        for r in blob["results"]:
            b = r["box"]
            recs.append({"task": "qa_vision_point", "case": r["id"], "frame": r["frame"],
                         "text": r["target"],
                         "truth": f"{b['x']},{b['y']},{b['w']},{b['h']}",
                         "model": m, "response": str(r.get("said")),
                         "correct": r["outcome"] == "hit", "cost": r["cost"],
                         "seconds": r["seconds"], "tokens": 0,
                         "outcome": r["outcome"],
                         "needs_defect_sight": False})
    return recs


def cost_rank(recs):
    """Rank models by measured cost per call, on a log scale — the pool spans
    three orders of magnitude and a linear rank would put everything but the
    dearest model at zero."""
    per = {}
    for r in recs:
        per.setdefault(r["model"], []).append(r["cost"])
    avg = {m: (sum(v) / len(v)) or 1e-9 for m, v in per.items()}
    lo, hi = math.log(min(avg.values())), math.log(max(avg.values()))
    span = (hi - lo) or 1.0
    return {m: (math.log(c) - lo) / span for m, c in avg.items()}, avg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", choices=["cost-aware", "raw"], default="cost-aware")
    ap.add_argument("--task", default="qa_vision_assert")
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    recs = [r for r in collect() if r["task"] == a.task]
    if not recs:
        raise SystemExit(f"no runs for {a.task}")
    rank, avg_cost = cost_rank(recs)
    out = os.path.join(OUT, f"{a.task}__{a.label}")
    os.makedirs(out, exist_ok=True)

    # queries, uniquely identified by their frame — see fix 1
    cases = {}
    for r in recs:
        cases.setdefault(r["case"], {"case": r["case"], "frame": r["frame"],
                                     "text": r["text"], "truth": r["truth"],
                                     "needs_defect_sight": r["needs_defect_sight"]})
    ordered = sorted(cases.values(), key=lambda c: c["case"])
    for i, c in enumerate(ordered):
        c["query"] = f"[{c['frame']}] {c['text']}"
        c["embedding_id"] = i
    by_case = {c["case"]: c for c in ordered}

    rnd = random.Random(a.seed)
    shuffled = ordered[:]
    rnd.shuffle(shuffled)
    cut = int(len(shuffled) * (1 - a.test_frac))
    train_ids = {c["case"] for c in shuffled[:cut]}

    def performance(r):
        if a.label == "raw":
            return 1.0 if r["correct"] else 0.0
        # correctness dominates; price only orders the models that were right
        return round(1.0 - 0.5 * rank[r["model"]], 6) if r["correct"] else 0.0

    rows = {"train": [], "test": []}
    for r in recs:
        c = by_case[r["case"]]
        rows["train" if r["case"] in train_ids else "test"].append({
            "task_name": a.task,
            "query": c["query"],
            "ground_truth": r["truth"],
            "metric": a.task,
            "model_name": r["model"],
            "response": r["response"],
            "performance": performance(r),
            "embedding_id": c["embedding_id"],
            "token_num": r["tokens"],
            # ours, carried alongside — a float cannot hold these
            "correct": r["correct"],
            "cost_usd": r["cost"],
            "seconds": r["seconds"],
            "frame": r["frame"],
            "needs_defect_sight": r["needs_defect_sight"],
        })

    for split in ("train", "test"):
        with open(os.path.join(out, f"routing_{split}.jsonl"), "w") as f:
            for r in rows[split]:
                f.write(json.dumps(r) + "\n")
        with open(os.path.join(out, f"query_{split}.jsonl"), "w") as f:
            for c in ordered:
                if (c["case"] in train_ids) == (split == "train"):
                    f.write(json.dumps({"query": c["query"], "task_name": a.task,
                                        "ground_truth": c["truth"],
                                        "embedding_id": c["embedding_id"]}) + "\n")

    with open(os.path.join(out, "llm_data.json"), "w") as f:
        json.dump({m: {"cost_per_call_usd": round(avg_cost[m], 8),
                       "cost_rank": round(rank[m], 4)} for m in sorted(avg_cost)}, f, indent=1)

    print(f"task {a.task} · label {a.label}")
    print(f"  {len(ordered)} unique cases (query carries its frame; without that "
          f"they would collapse to {len({c['text'] for c in ordered})})")
    print(f"  {len(rows['train'])} train / {len(rows['test'])} test records "
          f"over {len(avg_cost)} models")
    top = {}
    for c in ordered:
        cand = [r for r in rows["train"] + rows["test"] if r["embedding_id"] == c["embedding_id"]]
        if cand:
            best = max(cand, key=lambda r: r["performance"])
            if best["performance"] > 0:
                top[best["model_name"]] = top.get(best["model_name"], 0) + 1
    print(f"  what argmax(performance) now points at:")
    for m, n in sorted(top.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>4}  {m}")
    print(f"\n  → {out}")


if __name__ == "__main__":
    main()
