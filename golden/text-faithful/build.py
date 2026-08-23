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

# **An allowlist, because the denylist that used to be here failed exactly as
# denylists do.** The comment above this function has always read "documents the
# vault OWNS and that describe systems rather than people" — and the code
# implemented that by walking the whole vault and skipping seven named things.
# Anything nobody thought to name walked straight in.
#
# Measured 2026-08-23, on the set already committed and pushed: **585,755
# characters across 149 documents**, almost all of it a paid third party's
# course — module content, NotebookLM summaries, closed captions, and
# office-hours transcripts carrying a third party's name 43 times as a speaker.
# Nobody chose that. The filter simply did not have that project on its list.
#
# So the rule is inverted. A document is used only if it sits under a root the
# vault WROTE. New material is excluded until somebody adds it deliberately,
# which is the failure direction that costs a smaller corpus rather than
# somebody else's copyright.
OWNED_ROOTS = (
    "CLAUDE.md", "Home.md", "AGENTS.md", "GEMINI.md", "README.md",
    "05-Orchestrator",          # the vault's own system layer
    "01-Knowledge Base",        # our distilled reference
)

# Even inside an owned root, two things never belong in a corpus that may be
# published: material distilled FROM somewhere else, and anything that is a
# record of money, credentials or people.
OWNED_EXCEPT = (
    "Ingested",                 # third-party sources, distilled but not ours
    "Recall Pipeline",          # same
    "Infrastructure Ledger",    # spend and account names
    "External Dependencies",    # third-party product names by definition
    "Queue.md", "Open Board",   # cross-project chatter, names of other work
    "Vault Changelog",          # same
    "keys", "ledger",
    # Distilled FROM other people's publications, which is the same objection
    # as the course material — our sentences, somebody else's reporting, and
    # their bylines. Caught on the first clean rebuild: the digests carried a
    # named journalist 80 times purely because he presents a podcast we read.
    "digests", "Sources.md", "staged", "funnel",
    # Working state, not writing. `state/` also holds dated BACKUPS of the
    # knowledge base, so sampling it puts near-duplicates of the same passage
    # into the exam eight times over — which inflates the case count while
    # measuring one passage, the same error as counting a duplicated screen.
    "state", "runs", "backups",
)

# A last guard at the passage level, because a path rule cannot see inside a
# file. Transcripts are the specific shape that got through, and they are
# recognisable without knowing whose they are.
TRANSCRIPT = re.compile(
    r"\d{1,2}:\d{2}:\d{2}[.,]\d{3}"      # 01:16:05.710
    r"|-->"                                # WEBVTT cue arrow
    r"|^\s*\d{1,4}\s*$"                   # bare cue numbers
    r"|\b[a-z]+ [a-z]+:\s",                # a speaker label
    re.M)


def owned(path):
    """Is this a document the vault itself wrote?"""
    if any(x in path for x in OWNED_EXCEPT):
        return False
    return any(path == r or path.startswith(r + os.sep) or path.startswith(r + "/")
               for r in OWNED_ROOTS)


def sources(vault):
    """Every substantial vault-authored document about systems.

    Discovered rather than hand-listed, so the corpus can still grow — but only
    within roots the vault wrote. See OWNED_ROOTS for why that direction.
    """
    skip = ("node_modules", ".git", "__pycache__")
    out = []
    for root, dirs, files in os.walk(vault):
        rel = os.path.relpath(root, vault)
        if any(s in rel for s in skip):
            dirs[:] = []
            continue
        for f in files:
            if not f.endswith(".md"):
                continue
            path = os.path.join(rel, f) if rel != "." else f
            if not owned(path):
                continue
            if os.path.getsize(os.path.join(root, f)) < 2500:
                continue
            out.append(path)
    if not out:
        raise SystemExit(
            "no vault-authored documents found. This builder takes passages only "
            "from roots the vault wrote (see OWNED_ROOTS) — point --vault at a "
            "real vault, or add a root you own.")
    return sorted(out)

SENT = re.compile(r"(?<=[.!?])\s+")


def passages(vault, want, rnd):
    """Contiguous runs of real sentences. Long enough to need reading, short
    enough that a wrong answer is about the claim and not about attention."""
    out, refused = [], []
    for rel in sources(vault):
        path = os.path.join(vault, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        text = re.sub(r"```.*?```", " ", text, flags=re.S)      # code blocks
        # HTML comments are metadata, not prose — pipeline residue, TODOs and
        # provenance cards. A faithfulness exam should ask about sentences a
        # person wrote to be read. This also happens to be where a podcast
        # host's name was sitting 90 times, which is the second reminder today
        # that "our own file" is not the same as "nothing of anyone else's".
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
        text = re.sub(r"^\s*[-|#>*].*$", " ", text, flags=re.M)  # tables, lists, headings
        text = re.sub(r"\[\[.*?\]\]|\[.*?\]\(.*?\)", " ", text)  # links
        text = re.sub(r"[*_`]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        sents = [s.strip() for s in SENT.split(text) if 60 < len(s.strip()) < 320]
        # sliding window rather than fixed blocks — the same document yields
        # several passages without any two being the same text
        for i in range(0, max(0, len(sents) - 3), 2):
            chunk = " ".join(sents[i:i + 4])
            if not (300 < len(chunk) < 1200):
                continue
            # Belt and braces: a path rule cannot see inside a file, and the
            # thing that got through last time was recognisable by shape.
            if TRANSCRIPT.search(chunk):
                refused.append((rel, "reads as a transcript of people speaking"))
                continue
            out.append({"doc": rel, "text": chunk})
    if refused:
        print(f"  refused {len(refused)} passage(s) that read as transcripts")
    rnd.shuffle(out)
    return out[:want]


def looks_like_a_name(tok):
    """Real identifiers only. An ordinary word that happens to be capitalised
    makes nonsense when swapped, not a falsehood."""
    if tok.lower() in spec.NOT_NAMES:
        return False
    # A plain capitalised English word is usually a sentence opener, not a name —
    # "Missing" passed a length test once and produced a nonsense swap. Require
    # actual identifier shape: an internal capital, a digit, or punctuation.
    return (any(c.isupper() for c in tok[1:]) or any(c.isdigit() for c in tok)
            or "." in tok or "-" in tok or "_" in tok)


def other_names(all_passages, exclude_doc, rnd):
    names = set()
    for p in all_passages:
        if p["doc"] == exclude_doc:
            continue
        names.update(m.group(1) for m in
                     spec.CORRUPTIONS[-1]["pattern"].finditer(p["text"])
                     if looks_like_a_name(m.group(1)))
    return sorted(names)


def corrupt_hard(text, rule, ctx, rnd):
    """The classes that do not contradict the source. Ground truth stays exact
    because the edit is still mechanical — what changes is that a model cannot
    find it by spotting an inconsistency."""
    if rule["id"] == "unsupported-addition":
        cands = [s for s in ctx["foreign_sentences"] if 70 < len(s) < 240]
        if not cands:
            return None
        add = rnd.choice(cands)
        return text.rstrip() + " " + add, {"was": "(nothing)", "now": add[:60], "at": len(text)}
    if rule["id"] == "entity-reassign":
        names = [m.group(1) for m in spec.CORRUPTIONS[-1]["pattern"].finditer(text)
                 if looks_like_a_name(m.group(1))]
        uniq = sorted(set(names))
        if len(uniq) < 2:
            return None
        a, b = rnd.sample(uniq, 2)
        swapped = re.sub(rf"\b{re.escape(a)}\b", "\x00", text)
        swapped = re.sub(rf"\b{re.escape(b)}\b", a, swapped).replace("\x00", b)
        if swapped == text:
            return None
        return swapped, {"was": f"{a}/{b}", "now": f"{b}/{a}", "at": text.find(a)}
    return corrupt(text, rule, ctx["names"], rnd)


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




# ── The faithful side has to be hard too ──────────────────────────────────────
#
# Measured on the previous build: seven models, false-alarm rate 0-3% for every
# one of them. An axis where nothing ever happens is not measuring anything.
#
# The cause was in the design, not the models. A faithful case was the source
# text VERBATIM, so the question was "is this passage supported by itself" — a
# model would have to be broken to say no. Real distillation never hands you a
# copy; it hands you a rewrite, and the hard judgement is *this says the same
# thing in different words* versus *this has quietly added something*.
#
# So faithful cases are transformed too. Every transform below provably
# preserves support — reordering, dropping whole sentences, splitting one in
# two, removing a parenthetical aside — so ground truth stays exact and free
# while the case stops being a copy. Nothing here needs a model to generate and
# nothing needs a human to check.

def faithful_variants(text, rnd):
    """Rewrites that cannot introduce anything unsupported."""
    sents = [x.strip() for x in SENT.split(text) if x.strip()]
    out = []

    if len(sents) >= 3:                       # same claims, different order
        r = sents[:]
        rnd.shuffle(r)
        if r != sents:
            out.append(("reordered", " ".join(r)))

    if len(sents) >= 3:                       # a subset is still supported
        keep = sorted(rnd.sample(range(len(sents)), max(2, len(sents) - 2)))
        out.append(("shortened", " ".join(sents[i] for i in keep)))

    for i, snt in enumerate(sents):           # one sentence split at a comma
        if snt.count(",") >= 1 and len(snt) > 90:
            a, b = snt.split(",", 1)
            b = b.strip()
            if len(a) > 25 and len(b) > 25:
                new = sents[:]
                new[i] = f"{a.rstrip()}. {b[0].upper()}{b[1:]}"
                out.append(("split", " ".join(new)))
                break

    paren = re.search(r"\s*\([^()]{10,120}\)", text)   # drop an aside
    if paren:
        out.append(("aside-dropped", (text[:paren.start()] + text[paren.end():]).strip()))

    return out


def main():
    ap = argparse.ArgumentParser()
    # No default. It used to be the author's own vault path, so a stranger
    # running this got either nothing or, worse, a silent empty corpus. A
    # required argument asks the question; a default that exists on one machine
    # answers it wrongly everywhere else.
    ap.add_argument("--vault", required=True,
                    help="directory of .md documents to build the corpus from")
    ap.add_argument("--passages", type=int, default=40)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--per-class", type=int, default=None,
                    help="cases per corruption class; default is what the "
                         "rarest class can supply, so the split stays even")
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    per_class = a.per_class

    ps = passages(a.vault, a.passages, rnd)
    if not ps:
        raise SystemExit("no source passages found")
    cases, refused = [], []

    for p in ps:
        # the faithful case: the claim IS the source, so it cannot be unfaithful
        # One verbatim case keeps the easy end represented; the rest are
        # faithful REWRITES, which is what the task actually looks like.
        cases.append({"source": p["text"], "claim": p["text"], "answer": True,
                      "doc": p["doc"], "corruption": None, "variant": "verbatim",
                      "needs_defect_sight": False})
        for kind, claim in faithful_variants(p["text"], rnd):
            cases.append({"source": p["text"], "claim": claim, "answer": True,
                          "doc": p["doc"], "corruption": None, "variant": kind,
                          "needs_defect_sight": False})
        ctx = {"names": other_names(ps, p["doc"], rnd),
               "foreign_sentences": [s for q in ps if q["doc"] != p["doc"]
                                     for s in SENT.split(q["text"])]}
        for rule in spec.CORRUPTIONS + spec.HARD_CORRUPTIONS:
            hard = rule in spec.HARD_CORRUPTIONS
            got = (corrupt_hard(p["text"], rule, ctx, rnd) if hard
                   else corrupt(p["text"], rule, ctx["names"], rnd))
            if not got:
                refused.append((p["doc"], rule["id"], "nothing of that kind in the passage"))
                continue
            claim, note = got
            cases.append({"source": p["text"], "claim": claim, "answer": False,
                          "doc": p["doc"], "corruption": rule["id"],
                          "difficulty": spec.DIFFICULTY[rule["id"]],
                          "change": note, "needs_defect_sight": True})

    # Two things have to be true at once, and only one of them was.
    #
    # (1) 50/50 true/false, or "FALSE" every time wins the exam.
    # (2) **Every corruption class carried by roughly the same number of cases.**
    #
    # (2) was left to chance and chance is uneven: measured on the previous
    # build, `unsupported-addition` supplied 169 cases and `scope-widen` 39 —
    # not a judgement about what matters, just which corruptions happened to
    # find somewhere to apply. And the counts run the wrong way round. Ranked by
    # how often every model MISSES them:
    #
    #     scope-widen        41% missed     39 cases
    #     quantifier-flip    29% missed     52 cases
    #     unsupported-add    14% missed    169 cases
    #     unit-swap           8% missed     13 cases
    #
    # The classes that actually separate one model from another were the rarest,
    # so the exam spent most of itself on questions nobody gets wrong. That is
    # how a set ends up scoring every model 90-100% and measuring nothing.
    #
    # Stratified instead: take an equal share from each class, up to what the
    # class can supply, and let a class that cannot fill its share hand the
    # remainder back. Each class is a distinct failure mode and the per-mode
    # rate is the number this project exists to produce — so an even split is
    # the design, not a convenience.
    broken_all = [c for c in cases if not c["answer"]]
    pools = {}
    for c in broken_all:
        pools.setdefault(c["corruption"], []).append(c)
    for v in pools.values():
        rnd.shuffle(v)

    # An even share only stays even while every class can still supply one. Ask
    # for more than the rarest class holds and the loop keeps drawing from the
    # abundant ones — measured at 400 passages, `unsupported-addition` reached
    # 400 cases while `scope-widen` was stuck at 26, which is the exact
    # imbalance this was written to remove.
    #
    # So the cap is explicit and the rarest class sets it. A class that cannot
    # fill its share is NAMED rather than quietly topped up from elsewhere,
    # because the shortfall is a fact about the corpus that the next person
    # needs — it says which failure mode this set cannot yet speak about.
    cap = per_class or min(len(v) for v in pools.values())
    picked, short = [], []
    for cls in sorted(pools):
        take = pools[cls][:cap]
        picked.extend(take)
        if len(take) < cap:
            short.append((cls, len(take), cap))
    rnd.shuffle(picked)
    want = len(picked)
    broken = picked
    fpools = {}
    for c in cases:
        if c["answer"]:
            fpools.setdefault(c["variant"], []).append(c)
    for v in fpools.values():
        rnd.shuffle(v)
    # same even-share rule as the corruptions — a variant that applies often
    # must not drown out one that applies rarely
    faithful, flive = [], dict(fpools)
    fwant = min(want, sum(len(v) for v in fpools.values()))
    while flive and len(faithful) < fwant:
        share = max(1, (fwant - len(faithful)) // len(flive))
        for k in list(flive):
            take = flive[k][:share]
            faithful.extend(take)
            flive[k] = flive[k][len(take):]
            if not flive[k]:
                del flive[k]
            if len(faithful) >= fwant:
                break
    faithful = faithful[:fwant]
    # (broken is `picked` from the stratified draw above — never broken_all,
    # which is the unstratified pool and was overwriting it here.)
    want = len(faithful)

    rnd.shuffle(broken)
    n = min(len(faithful), len(broken))
    rnd.shuffle(faithful)
    rnd.shuffle(broken)
    faithful, broken = faithful[:n], broken[:n]
    cases = faithful + broken
    rnd.shuffle(cases)
    for i, c in enumerate(cases, 1):
        c["id"] = f"T{i:03d}"
        c["assert"] = spec.QUESTION.format(source=c["source"], claim=c["claim"])

    t = sum(1 for c in cases if c["answer"])
    by, by_diff = {}, {}
    for c in broken:
        by[c["corruption"]] = by.get(c["corruption"], 0) + 1
        by_diff[c["difficulty"]] = by_diff.get(c["difficulty"], 0) + 1
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
                   "by_difficulty": by_diff,
                   "refused": len(refused),
                   "case_list": cases}, f, indent=1)
    print(f"{len(ps)} passages from {len({p['doc'] for p in ps})} documents")
    print(f"{len(cases)} cases · {t} true / {len(cases)-t} false · "
          f"{sum(1 for c in cases if c['needs_defect_sight'])} needing defect sight")
    print("by difficulty:", ", ".join(f"{k} {v}" for k, v in sorted(by_diff.items())))
    print("corruption classes used:", ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
    fv = {}
    for c in cases:
        if c["answer"]:
            fv[c["variant"]] = fv.get(c["variant"], 0) + 1
    if short:
        print("classes that could NOT fill their share — this set cannot speak\n  about these failure modes at full strength:")
        for cls, got, cap_ in short:
            print(f"    {cls:<24} {got}/{cap_}")
    print("faithful variants used:", ", ".join(f"{k} {v}" for k, v in sorted(fv.items())))
    print(f"{len(refused)} class/passage pairs refused as inapplicable")


if __name__ == "__main__":
    main()
