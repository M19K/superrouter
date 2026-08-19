#!/usr/bin/env python3
"""
label_eval.py — the label sets the ceiling. Measure the ceiling.

    python3 -m router.label_eval

A router can only learn to imitate its labels. So before training anything, ask
what a **perfect** router trained on each labelling scheme would do — follow its
label exactly on every held-out case and see what that costs and what it gets
right. Whatever the 16 algorithms achieve, they cannot beat this, and the gap
between two labelling schemes here is the whole value of changing the label.

Compared against the two things a router has to beat to be worth having:
always the dear model, and always the cheapest.
"""
import json
import os

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(CODE, "state", "llmrouter_corpus")


def load(task, label, split="test"):
    p = os.path.join(CORPUS, f"{task}__{label}", f"routing_{split}.jsonl")
    return [json.loads(l) for l in open(p)]


def oracle(rows):
    """Follow the label on every case: pick argmax(performance), ties by file
    order exactly as LLMRouter's trainer does."""
    by_case = {}
    for r in rows:
        by_case.setdefault(r["embedding_id"], []).append(r)
    correct = cost = 0.0
    picks = {}
    for cands in by_case.values():
        best = max(cands, key=lambda r: r["performance"])
        picks[best["model_name"]] = picks.get(best["model_name"], 0) + 1
        correct += 1 if best["correct"] else 0
        cost += best["cost_usd"]
    return len(by_case), correct, cost, picks


def fixed(rows, model):
    sel = [r for r in rows if r["model_name"] == model]
    return len(sel), sum(1 for r in sel if r["correct"]), sum(r["cost_usd"] for r in sel)


def main(task="qa_vision_assert"):
    raw, cost_aware = load(task, "raw"), load(task, "cost-aware")
    models = sorted({r["model_name"] for r in raw})
    per = {m: fixed(raw, m) for m in models}
    dearest = max(models, key=lambda m: per[m][2])
    cheapest = min(models, key=lambda m: per[m][2])

    print(f"held-out test split · {task} · "
          f"{len({r['embedding_id'] for r in raw})} cases, {len(models)} models\n")
    print(f"{'accuracy':>9} {'cost':>10} {'vs dearest':>11}  strategy")
    print("-" * 62)

    rows = []
    n, c, k = per[dearest]
    rows.append((round(100 * c / n), k, f"always {dearest.split('/')[-1]}"))
    base = k
    n, c, k = per[cheapest]
    rows.append((round(100 * c / n), k, f"always {cheapest.split('/')[-1]}"))
    for label, data in (("raw (LLMRouter's label)", raw),
                        ("cost-aware (ours)", cost_aware)):
        n, c, k, picks = oracle(data)
        rows.append((round(100 * c / n), k, f"perfect router on {label}"))
    for acc, k, name in rows:
        print(f"{acc:>8}% {k:>10.5f} {base / k if k else 0:>10.0f}×  {name}")

    print()
    for label, data in (("raw", raw), ("cost-aware", cost_aware)):
        n, c, k, picks = oracle(data)
        spread = ", ".join(f"{m.split('/')[-1]} {v}" for m, v in
                           sorted(picks.items(), key=lambda kv: -kv[1])[:3])
        print(f"  {label:<11} routes to: {spread}")

    n_r, c_r, k_r, _ = oracle(raw)
    n_c, c_c, k_c, _ = oracle(cost_aware)
    print(f"\nSame algorithms, same data, one field changed:")
    print(f"  accuracy {round(100*c_r/n_r)}% → {round(100*c_c/n_c)}%   "
          f"cost ${k_r:.5f} → ${k_c:.5f}   ({k_r/k_c:.0f}× cheaper)")
    print("\nThat gap is the ceiling any of their 16 algorithms is trained toward.")
    print("It is not an algorithm improvement. It is the label.")


if __name__ == "__main__":
    main()
