#!/usr/bin/env python3
"""
build.py — generate the text-faithfulness golden set from real vault documents.

    python3 build.py [--vault ~/Documents/Mikoshi] [--passages 40]

Takes verbatim passages out of real documents, so the faithful case is faithful
by construction. Plants exactly one mechanical falsehood per corrupted case, so
the unfaithful case is unfaithful by construction. Neither needs a labeller.

**A corruption that changes nothing is refused**, the same rule the vision set
enforces with pixels: here, if the corrupted text equals the original, or the
edit lands somewhere that leaves the claim still true, the case is dropped
rather than filed as a lie about itself.
"""
import argparse
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec

HERE = os.path.dirname(os.path.abspath(__file__))

# Documents the vault owns and that describe systems rather than people. The
# task is professional-facing; nothing personal goes into a corpus that may be
# published later.
SOURCES = [
    "05-Orchestrator/Information Lifecycle.md",
    "05-Orchestrator/funnel/README.md",
    "05-Orchestrator/qa/README.md",
    "05-Orchestrator/Dependencies.md",
    "05-Orchestrator/_index.md",
    "01-Knowledge Base/Infrastructure Ledger.md",
    "05-Orchestrator/jobs/README.md",
]

SENT = re.compile(r"(?<=[.!?])\s+")


def passages(vault, want, rnd):
    """Contiguous runs of real sentences. Long enough to need reading, short
    enough that a wrong answer is about the claim and not about attention."""
    out = []
    for rel in SOURCES:
        path = os.path.join(vault, rel)
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        text = re.sub(r"```.*?```", " ", text, flags=re.S)      # code blocks
        text = re.sub(r"^\s*[-|#>*].*$", " ", text, flags=re.M)  # tables, lists, headings
        text = re.sub(r"\[\[.*?\]\]|\[.*?\]\(.*?\)", " ", text)  # links
        text = re.sub(r"[*_`]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        sents = [s.strip() for s in SENT.split(text) if 60 < len(s.strip()) < 320]
        # sliding window rather than fixed blocks — the same document yields
        # several passages without any two being the same text
        for i in range(0, max(0, len(sents) - 3), 2):
            chunk = " ".join(sents[i:i + 4])
            if 300 < len(chunk) < 1200:
                out.append({"doc": rel, "text": chunk})
    rnd.shuffle(out)
    return out[:want]


def looks_like_a_name(tok):
    """Real identifiers only. An ordinary word that happens to be capitalised
    makes nonsense when swapped, not a falsehood."""
    if tok.lower() in spec.NOT_NAMES:
        return False
    # an internal capital, a digit, or a dot is strong evidence of a real name
    return (any(c.isupper() for c in tok[1:]) or any(c.isdigit() for c in tok)
            or "." in tok or "-" in tok or "_" in tok or len(tok) > 5)


def other_names(all_passages, exclude_doc, rnd):
    names = set()
    for p in all_passages:
        if p["doc"] == exclude_doc:
            continue
        names.update(m.group(1) for m in
                     spec.CORRUPTIONS[-1]["pattern"].finditer(p["text"])
                     if looks_like_a_name(m.group(1)))
    return sorted(names)


def corrupt(text, rule, pool, rnd):
    """Apply one corruption at one randomly chosen site. Returns the new text
    and a note of what was changed, or None when the class does not apply."""
    hits = list(rule["pattern"].finditer(text))
    if not hits:
        return None
    rnd.shuffle(hits)
    for m in hits:
        if rule["id"] == "entity-swap":
            if not looks_like_a_name(m.group(1)):
                continue
            cands = [n for n in pool if n.lower() != m.group(1).lower()]
            if not cands:
                continue
            repl = rnd.choice(cands)
        else:
            try:
                repl = rule["change"](m)
            except (KeyError, TypeError):
                continue
        if not repl or repl == m.group(0):
            continue
        new = text[:m.start()] + repl + text[m.end():]
        if new == text:
            continue
        return new, {"was": m.group(0), "now": repl, "at": m.start()}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=os.path.expanduser("~/Documents/Mikoshi"))
    ap.add_argument("--passages", type=int, default=40)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    rnd = random.Random(a.seed)

    ps = passages(a.vault, a.passages, rnd)
    if not ps:
        raise SystemExit("no source passages found")
    cases, refused = [], []

    for p in ps:
        # the faithful case: the claim IS the source, so it cannot be unfaithful
        cases.append({"source": p["text"], "claim": p["text"], "answer": True,
                      "doc": p["doc"], "corruption": None,
                      "needs_defect_sight": False})
        pool = other_names(ps, p["doc"], rnd)
        for rule in spec.CORRUPTIONS:
            got = corrupt(p["text"], rule, pool, rnd)
            if not got:
                refused.append((p["doc"], rule["id"], "nothing of that kind in the passage"))
                continue
            claim, note = got
            cases.append({"source": p["text"], "claim": claim, "answer": False,
                          "doc": p["doc"], "corruption": rule["id"],
                          "change": note, "needs_defect_sight": True})

    # balance: one faithful case per passage against six corrupted ones would
    # make "FALSE" the winning constant answer. Trim corruptions to match.
    faithful = [c for c in cases if c["answer"]]
    broken = [c for c in cases if not c["answer"]]
    rnd.shuffle(broken)
    broken = broken[:len(faithful)]
    cases = faithful + broken
    rnd.shuffle(cases)
    for i, c in enumerate(cases, 1):
        c["id"] = f"T{i:03d}"
        c["assert"] = spec.QUESTION.format(source=c["source"], claim=c["claim"])

    t = sum(1 for c in cases if c["answer"])
    by = {}
    for c in broken:
        by[c["corruption"]] = by.get(c["corruption"], 0) + 1
    with open(os.path.join(HERE, "manifest.json"), "w") as f:
        json.dump({"task_type": "text-faithful", "version": 1,
                   "built": __import__("time").strftime("%Y-%m-%d"),
                   "source": "real vault documents, verbatim",
                   "what_the_model_is_asked":
                       "Given a source passage and a claim about it, say whether every "
                       "statement in the claim is supported by the source.",
                   "cases": len(cases), "true": t, "false": len(cases) - t,
                   "defect_sight_cases": sum(1 for c in cases if c["needs_defect_sight"]),
                   "corruptions_used": by,
                   "refused": len(refused),
                   "case_list": cases}, f, indent=1)
    print(f"{len(ps)} passages from {len({p['doc'] for p in ps})} documents")
    print(f"{len(cases)} cases · {t} true / {len(cases)-t} false · "
          f"{sum(1 for c in cases if c['needs_defect_sight'])} needing defect sight")
    print("corruption classes used:", ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
    print(f"{len(refused)} class/passage pairs refused as inapplicable")


if __name__ == "__main__":
    main()
