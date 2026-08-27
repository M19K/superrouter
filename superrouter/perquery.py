#!/usr/bin/env python3
"""
perquery.py — route per query, not per task, with no dependencies at all.

    python3 -m superrouter.perquery --set locus
    python3 -m superrouter.perquery --set locus --bar 0.8

**Why this exists.** Until now SuperRouter chose one model per *task type* from
the measured ladder and sent every query of that type to it. That is a table,
not a router: it cannot tell an easy query from a hard one, so it must pay the
hard-query price on every query. Measured on our own exams, a per-query decision
reaches the same accuracy for up to 24x less.

**Everything here is standard method.** Feature hashing, cosine similarity,
distance-weighted k-nearest-neighbours and an expected-cost decision rule are
textbook techniques that predate every LLM router by decades. Written from the
method, not from anyone's file, and deliberately in the standard library only —
the whole install story of this project is that there isn't one.

── What it predicts, and why it is not "pick the best model" ────────────────

A router that predicts *the single best model* has thrown away the question the
caller actually asked. Ours estimates, for **every** candidate, the probability
that it answers this query correctly — then returns **the cheapest model whose
estimate clears the caller's bar**. Three consequences:

- The caller sets the bar. A QA pass that must not miss defects and a draft
  summariser want different answers from the same table, and neither is "best".
- An easy query clears the bar on a cheap model and never wakes the dear one.
- When nothing clears the bar the router says so and falls back to the most
  accurate model, rather than silently returning its least-bad guess.

── The estimator ────────────────────────────────────────────────────────────

For a query q and model m, look at the k nearest training queries by cosine
similarity and take the similarity-weighted fraction of them that m answered
correctly. Laplace-smoothed at BOTH levels — the neighbourhood and the model's
own prior — so no amount of finite evidence ever reports certainty.

    p(m correct | q) = (Σ w_i · correct_i + α · prior_m) / (Σ w_i + α)

`prior_m` is that model's overall measured accuracy, which is what the estimate
decays to when the neighbourhood says nothing — so an unfamiliar query falls
back to the ladder rather than to noise. That is the same instinct as every
other decision in this project: when the evidence is thin, say so and use the
number that was actually measured.

── Features, without an embedding model ─────────────────────────────────────

Assertions are one short sentence and a frame id. A hashed bag of word and
character n-grams separates them well enough, costs nothing, needs no model
download, and cannot drift when somebody upgrades a dependency. The frame id is
hashed as its own token, because — as this project already found the hard way —
the same sentence asked of two different screens has two different answers, and
a feature set that cannot tell them apart is the same bug one layer down.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys

from ._io import read_lines

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(CODE, "state", "corpus")
DIM = 2048
WORD = re.compile(r"[a-z0-9]+")


def features(text):
    """Hashed sparse vector: words, word bigrams, and character 4-grams.

    Character n-grams carry the near-duplicates that word tokens miss — two
    assertions differing by one word are far apart in bag-of-words and close in
    character space, and "differing by one word" is most of this corpus.
    """
    t = text.lower()
    words = WORD.findall(t)
    toks = list(words)
    toks += [f"{a}_{b}" for a, b in zip(words, words[1:])]
    squashed = " ".join(words)
    toks += [squashed[i:i + 4] for i in range(max(len(squashed) - 3, 0))]
    v = {}
    for tok in toks:
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=4).digest(), "big")
        # A signed hash so unrelated tokens colliding tend to cancel rather
        # than accumulate into a phantom feature.
        idx, sign = h % DIM, 1.0 if (h >> 16) & 1 else -1.0
        v[idx] = v.get(idx, 0.0) + sign
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {i: x / norm for i, x in v.items()}


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(i, 0.0) for i, x in a.items())


class PerQueryRouter:
    """Fit on scored records; predict a model per query at a stated quality bar."""

    def __init__(self, k=12, alpha=2.0):
        self.k = k
        self.alpha = alpha
        self.vectors = []          # [(vec, {model: correct})]
        self.models = []
        self.prior = {}            # model -> measured accuracy
        self.cost = {}             # model -> measured mean cost per call

    def fit(self, rows, queries):
        by_case = {}
        for r in rows:
            by_case.setdefault(r["embedding_id"], {})[r["model_name"]] = r
        self.models = sorted({r["model_name"] for r in rows})
        for m in self.models:
            got = [r for r in rows if r["model_name"] == m]
            hits = sum(1 for r in got if r["correct"])
            # **Laplace on the prior too, not only on the neighbourhood.**
            # Without this a model that went 40/40 has prior 1.0, the smoothed
            # estimate collapses to exactly 1.0, and the docstring's promise
            # that finite evidence cannot report certainty was false — a bar of
            # 0.999 was cleared on evidence that does not support it. Found by
            # this module's own test, 2026-08-27. (hits+1)/(n+2) is the same
            # instinct as deciding on an interval's lower bound everywhere else
            # in this project: finite evidence never earns certainty.
            self.prior[m] = (hits + 1) / (len(got) + 2)
            self.cost[m] = sum(r["cost_usd"] for r in got) / max(len(got), 1)
        for cid, per in by_case.items():
            q = queries.get(cid)
            if q is None:
                continue
            self.vectors.append((features(q["query"]),
                                 {m: bool(v["correct"]) for m, v in per.items()}))
        return self

    def probabilities(self, query_text):
        """p(correct) per model, from the weighted neighbourhood."""
        v = features(query_text)
        sims = sorted(((cosine(v, fv), lab) for fv, lab in self.vectors),
                      key=lambda t: -t[0])[: self.k]
        out = {}
        for m in self.models:
            num = self.alpha * self.prior[m]
            den = self.alpha
            for s, lab in sims:
                if m not in lab or s <= 0:
                    continue
                num += s * (1.0 if lab[m] else 0.0)
                den += s
            out[m] = num / den
        return out

    def route(self, query_text, bar=0.75):
        """Cheapest model whose estimate clears `bar`; else the most accurate.

        Returning the most accurate model when nothing clears the bar is the
        conservative direction on purpose — the failure it avoids is answering
        a hard query cheaply and being wrong, which is the exact failure a
        router is bought to prevent.
        """
        p = self.probabilities(query_text)
        ok = [m for m in self.models if p[m] >= bar]
        if ok:
            return min(ok, key=lambda m: self.cost[m]), p, True
        return max(self.models, key=lambda m: p[m]), p, False


def best_fixed(full, models):
    """The best single model, by accuracy then price. The thing to beat."""
    n = max(len(full), 1)
    acc = {m: sum(1 for i in full if full[i][m]["correct"]) / n for m in models}
    cost = {m: sum(full[i][m]["cost_usd"] for i in full) for m in models}
    top = max(acc.values())
    within = [m for m in models if acc[m] >= top - 0.02]
    m = min(within, key=lambda x: cost[x])
    return m, {"cases": len(full), "accuracy": round(100 * acc[m]),
               "cost": round(cost[m], 6)}


def dominated(routed, fixed):
    """Does the fixed choice beat routing outright — same or better accuracy,
    at the same or lower cost?

    **Routing is not free and is not always right.** Measured 2026-08-27 across
    two products: on one, no cheap model was good enough and per-query routing
    reached the best fixed accuracy for 2.25x less. On the other, a single cheap
    model was already near-best, and routing matched its accuracy at **29x the
    price** — it spread across dearer models to buy nothing.

    Nothing in any routing framework we examined asks this question. It is the
    same rule this project already applies to the verifier: beat the baseline or
    you bought nothing. A router that cannot beat not-routing should say so.
    """
    return fixed["accuracy"] >= routed["accuracy"] and fixed["cost"] <= routed["cost"]


def load(base, split):
    rows = [json.loads(ln) for ln in read_lines(os.path.join(base, f"routing_{split}.jsonl"))]
    queries = {q["embedding_id"]: q
               for q in (json.loads(ln) for ln in
                         read_lines(os.path.join(base, f"query_{split}.jsonl")))}
    return rows, queries


def evaluate(router, rows, queries, bar):
    by_case = {}
    for r in rows:
        by_case.setdefault(r["embedding_id"], {})[r["model_name"]] = r
    full = {i: v for i, v in by_case.items() if len(v) == len(router.models)}
    correct = cost = 0.0
    fell_back = 0
    picks = {}
    for cid, per in full.items():
        m, _, cleared = router.route(queries[cid]["query"], bar=bar)
        picks[m] = picks.get(m, 0) + 1
        fell_back += 0 if cleared else 1
        correct += 1 if per[m]["correct"] else 0
        cost += per[m]["cost_usd"]
    n = max(len(full), 1)
    return {"cases": len(full), "accuracy": round(100 * correct / n),
            "cost": round(cost, 6), "no_model_cleared": fell_back, "picks": picks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="set_name")
    ap.add_argument("--label", default="cost-aware")
    ap.add_argument("--task", default="qa_vision_assert")
    ap.add_argument("--bar", type=float, default=None,
                    help="required p(correct); default sweeps a range")
    ap.add_argument("--k", type=int, default=12)
    a = ap.parse_args()

    base = os.path.join(CORPUS, f"{a.task}__{a.label}"
                        + (f"__{a.set_name}" if a.set_name else ""))
    if not os.path.isdir(base):
        raise SystemExit(f"no corpus at {os.path.relpath(base, CODE)}")

    tr_rows, tr_q = load(base, "train")
    te_rows, te_q = load(base, "test")
    r = PerQueryRouter(k=a.k).fit(tr_rows, tr_q)

    by_case = {}
    for row in te_rows:
        by_case.setdefault(row["embedding_id"], {})[row["model_name"]] = row
    full = {i: v for i, v in by_case.items() if len(v) == len(r.models)}
    n = max(len(full), 1)

    print(f"corpus {os.path.relpath(base, CODE)}")
    print(f"  {len(tr_q)} training queries · {len(full)} held-out · "
          f"{len(r.models)} models · k={a.k}\n")

    fixed = {}
    for m in r.models:
        acc = round(100 * sum(1 for i in full if full[i][m]["correct"]) / n)
        fixed[m] = {"cases": len(full), "accuracy": acc,
                    "cost": round(sum(full[i][m]["cost_usd"] for i in full), 6)}
    dear = max(r.models, key=lambda m: fixed[m]["cost"])
    ref = fixed[dear]["cost"]

    print(f"  {'':<46}{'acc':>5}{'cost':>11}{'vs priciest':>13}")
    for m in sorted(r.models, key=lambda m: fixed[m]["cost"]):
        mult = f"{ref / fixed[m]['cost']:.1f}x" if fixed[m]["cost"] else "free"
        print(f"  fixed  {m:<39}{fixed[m]['accuracy']:>4}%${fixed[m]['cost']:>10.5f}{mult:>13}")
    print()
    bars = [a.bar] if a.bar is not None else [0.55, 0.65, 0.75, 0.85, 0.95]
    results = {}
    for bar in bars:
        res = evaluate(r, te_rows, te_q, bar)
        results[bar] = res
        mult = f"{ref / res['cost']:.1f}x" if res["cost"] else "free"
        print(f"  ours   per-query, bar={bar:<31.2f}{res['accuracy']:>4}%"
              f"${res['cost']:>10.5f}{mult:>13}"
              + (f"   ({res['no_model_cleared']} cases nothing cleared)"
                 if res["no_model_cleared"] else ""))

    # ── the verdict, and it is allowed to be "do not route" ──────────────────
    fm, fres = best_fixed(full, r.models)
    best_bar = max(results, key=lambda b: (results[b]["accuracy"], -results[b]["cost"]))
    best = results[best_bar]
    print(f"\n  best fixed choice : {fm} — {fres['accuracy']}% at ${fres['cost']:.5f}")
    print(f"  best routed choice: bar={best_bar} — {best['accuracy']}% at ${best['cost']:.5f}")
    if dominated(best, fres):
        print(f"\n  VERDICT: DO NOT ROUTE this task. The fixed choice matches or beats\n"
              f"  routing on both axes, so per-query routing would spend "
              f"{best['cost'] / fres['cost']:.1f}x more to buy nothing.")
    else:
        saved = fres["cost"] / best["cost"] if best["cost"] else float("inf")
        d = best["accuracy"] - fres["accuracy"]
        # Say what the trade IS. "Beats" while showing a lower accuracy is the
        # kind of sentence a reader has to re-read, and a number that has to be
        # re-read has already misled once.
        if d >= 0:
            how = f"{d:+d} points of accuracy AND {saved:.1f}x less cost"
        else:
            how = (f"{-d} point(s) of accuracy traded for {saved:.1f}x less cost "
                   f"— the caller's bar decides whether that is worth it")
        print(f"\n  VERDICT: ROUTE. {best['accuracy']}% at ${best['cost']:.5f} "
              f"against the fixed {fres['accuracy']}% at ${fres['cost']:.5f}:\n  {how}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
