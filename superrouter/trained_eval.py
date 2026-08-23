#!/usr/bin/env python3
"""
trained_eval.py — score a router LLMRouter actually trained, on held-out cases.

    python3 -m superrouter.trained_eval

The label sets the ceiling (see `label_eval.py`); this measures what a real
trained router reaches. For each held-out case the router predicts a model from
the query embedding alone — it has never seen which models got that case right —
and we look up what that model actually answered and what it actually cost.

Compared against always-dearest, always-cheapest, and the label's own ceiling,
because a router that beats neither fixed strategy is not worth running.
"""
import json
import os
import pickle

try:
    import torch
except ImportError:                      # pragma: no cover
    raise SystemExit(
        "This module reads a model LLMRouter trained, which is stored as a\n"
        "PyTorch tensor — so it needs `pip install llmrouter-lib`, and that\n"
        "pulls in torch.\n\n"
        "Nothing else in SuperRouter needs it. The router, the measurement,\n"
        "the cascade and the dashboard are standard library only.")

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(CODE, "state", "llmrouter_corpus")
TASK = "qa_vision_assert"


def evaluate(label, router_file="models/knn.pkl"):
    base = os.path.join(CORPUS, f"{TASK}__{label}")
    model = pickle.load(open(os.path.join(base, router_file), "rb"))
    emb = torch.load(os.path.join(base, "query_embeddings.pt"))
    rows = [json.loads(l) for l in open(os.path.join(base, "routing_test.jsonl"))]

    lookup = {}
    for r in rows:
        lookup[(r["embedding_id"], r["model_name"])] = r
    ids = sorted({r["embedding_id"] for r in rows})

    correct = cost = 0.0
    unseen = 0
    picks = {}
    for i in ids:
        pred = model.predict([emb[i].numpy()])[0]
        picks[pred] = picks.get(pred, 0) + 1
        rec = lookup.get((i, pred))
        if rec is None:                    # predicted a model not scored here
            unseen += 1
            continue
        correct += 1 if rec["correct"] else 0
        cost += rec["cost_usd"]
    return {"cases": len(ids), "accuracy": round(100 * correct / len(ids)),
            "cost": cost, "picks": picks, "unseen": unseen}


def fixed(label, model_name):
    base = os.path.join(CORPUS, f"{TASK}__{label}")
    rows = [json.loads(l) for l in open(os.path.join(base, "routing_test.jsonl"))
            if json.loads(l)["model_name"] == model_name]
    return {"cases": len(rows),
            "accuracy": round(100 * sum(r["correct"] for r in rows) / len(rows)),
            "cost": sum(r["cost_usd"] for r in rows)}


def main():
    base = os.path.join(CORPUS, f"{TASK}__raw")
    rows = [json.loads(l) for l in open(os.path.join(base, "routing_test.jsonl"))]
    models = sorted({r["model_name"] for r in rows})
    per = {m: fixed("raw", m) for m in models}
    dear = max(models, key=lambda m: per[m]["cost"])
    cheap = min(models, key=lambda m: per[m]["cost"])
    baseline = per[dear]["cost"]

    print(f"KNN router trained by LLMRouter · held-out test split · "
          f"{per[dear]['cases']} cases\n")
    print(f"{'accuracy':>9} {'cost':>10} {'vs dearest':>11}  strategy")
    print("-" * 66)
    print(f"{per[dear]['accuracy']:>8}% {per[dear]['cost']:>10.5f} "
          f"{1:>10}×  always {dear.split('/')[-1]}")
    print(f"{per[cheap]['accuracy']:>8}% {per[cheap]['cost']:>10.5f} "
          f"{baseline/per[cheap]['cost']:>10.0f}×  always {cheap.split('/')[-1]}")
    out = {}
    for label in ("raw", "cost-aware"):
        r = evaluate(label)
        out[label] = r
        print(f"{r['accuracy']:>8}% {r['cost']:>10.5f} "
              f"{baseline/r['cost'] if r['cost'] else 0:>10.0f}×  "
              f"trained KNN, {label} label")
    print()
    for label, r in out.items():
        spread = ", ".join(f"{m.split('/')[-1]} {v}" for m, v in
                           sorted(r["picks"].items(), key=lambda kv: -kv[1])[:3])
        print(f"  {label:<11} predicts: {spread}")
    a, b = out["raw"], out["cost-aware"]
    print(f"\nSame algorithm, same embeddings, same held-out cases. One field changed:")
    print(f"  accuracy {a['accuracy']}% → {b['accuracy']}%   "
          f"cost ${a['cost']:.5f} → ${b['cost']:.5f}   "
          f"({a['cost']/b['cost']:.0f}× cheaper)")


if __name__ == "__main__":
    main()
