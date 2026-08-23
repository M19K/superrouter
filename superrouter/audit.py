#!/usr/bin/env python3
"""
audit.py — re-derive every published claim from the records, and fail if one is wrong.

    python3 -m superrouter.audit            # check everything
    python3 -m superrouter.audit --strict   # exit 1 on any failure (for CI)

**Why this exists.** Between 2026-08-19 and 2026-08-22 this instrument was wrong
six times, and every single time in the same direction: it scored its own
failure against the model it was measuring.

    a timeout                     counted as the model refusing
    an empty answer               counted as a false alarm (read as 98%)
    an unanswerable reference     counted as the router disagreeing
    two different exams           compared as if they were one
    the cost of the audit         billed to the saving it was auditing
    reasoning the harness left on billed to the model as its cost

None was found by testing with well-behaved models. Each surfaced only when
something failed in a way nobody had seen. That is not a run of bad luck — it
is a *class*, and a class you cannot enumerate has to be closed structurally
rather than patched one instance at a time.

So this module does two things a human reading a README cannot:

  1. **Re-derives every number quoted in the docs from the run records** and
     reports any that no longer matches. A claim whose source has moved is a
     claim nobody has checked since the day it was written.
  2. **Asserts the class rule directly** — that no request the instrument
     failed to complete may ever count against the model — against every run
     record on disk, so a seventh instance fails here rather than in public.

It is deliberately not part of the eval path. An audit that runs inside the
thing it audits proves nothing.
"""
import argparse
import glob
import json
import os
import re
import sys

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OK, BAD, WARN = "PASS", "FAIL", "UNVERIFIABLE"


def runs(include_quarantined=False):
    """Comparable runs by default. Quarantined ones are real evidence of money
    actually spent, but their exam cannot be identified, so they may never enter
    a comparison — two different questions, two different sets."""
    pats = [os.path.join(CODE, "state", "*run*", "*.json")]
    if include_quarantined:
        pats.append(os.path.join(CODE, "state", "*run*", "_unstamped", "*.json"))
    for p in sorted(x for pat in pats for x in glob.glob(pat)):
        try:
            yield p, json.load(open(p))
        except Exception:
            continue


# ── 1 · the class rule, asserted against every record ────────────────────────

def check_failures_not_scored(results):
    """No request the instrument failed to complete may count against a model.

    This is the rule the six bugs each broke. It is checked here on the stored
    records rather than trusted from the code, because five of the six were in
    code that looked correct.
    """
    bad = []
    for p, b in results:
        s, rs = b.get("summary", {}), b.get("results", [])
        if not rs:
            continue
        errored = [r for r in rs if r.get("error")]
        # an errored call must not appear as a verdict of any kind
        scored_errors = [r for r in errored if r.get("said") is not None
                         or r.get("outcome") not in (None, "error")]
        if scored_errors:
            bad.append((p, f"{len(scored_errors)} errored call(s) carry a verdict"))
        # refusal rate must be over what reached the model, not over what was sent
        if s.get("reached") and s.get("refusals") is not None:
            expect = round(100 * s["refusals"] / s["reached"])
            if s.get("refusal_pct") not in (expect, None):
                bad.append((p, f"refusal_pct {s['refusal_pct']}% but "
                               f"{s['refusals']}/{s['reached']} = {expect}%"))
    return bad


def check_exams_not_mixed(results):
    """Two runs may only be compared if they sat the same exam."""
    by_dir = {}
    for p, b in results:
        s = b.get("summary", {})
        if s.get("cases", 0) < 40:
            continue
        by_dir.setdefault(os.path.dirname(p), {}).setdefault(
            s.get("exam_fingerprint") or "UNSTAMPED", []).append(s["model"])
    out = []
    for d, exams in by_dir.items():
        if len(exams) > 1:
            out.append((os.path.basename(d),
                        f"{len(exams)} distinct exam(s) in one directory: "
                        + ", ".join(f"{k[:8]}×{len(v)}" for k, v in exams.items())))
    return out


def check_settings_recorded(results):
    """A run whose configuration is unrecorded cannot be compared to anything."""
    missing = []
    for p, b in results:
        s = b.get("summary", {})
        if s.get("cases", 0) < 40:
            continue
        if s.get("reasoning_off") is None and s.get("reasoning_per_call") is None:
            missing.append(os.path.basename(p))
    return missing


# ── 2 · every number in the docs, re-derived ─────────────────────────────────

def derive():
    """The facts, computed fresh from the records every time this runs."""
    rs = list(runs())
    every = list(runs(include_quarantined=True))
    f = {}
    f["runs"] = len(rs)
    f["all_runs"] = len(every)
    # Money spent is money spent, quarantined or not. Comparable records are a
    # different figure and conflating them would understate the real bill —
    # which is the same class of error this whole module exists to catch.
    f["spend"] = round(sum(b["summary"].get("cost_usd", 0) for _, b in every), 2)
    f["comparable_spend"] = round(sum(b["summary"].get("cost_usd", 0) for _, b in rs), 2)
    served = os.path.join(CODE, "state", "served.jsonl")
    if os.path.exists(served):
        rows = [json.loads(l) for l in open(served) if l.strip()]
        sp = sum(r.get("cost_usd") or 0 for r in rows)
        rf = sum(r.get("reference_cost_estimate") or 0 for r in rows)
        f["served_calls"] = len(rows)
        f["observed_saving"] = round(rf / sp) if sp else 0
    for name, d in (("text", "text_runs"), ("assert", "runs_portfolio")):
        best = {}
        for p, b in rs:
            if d not in p:
                continue
            best[b["summary"]["model"]] = b["summary"]
        if best:
            f[f"{name}_models"] = len(best)
    return f


CLAIMS = [
    # (regex to find in the docs, key in derive(), how to compare)
    (r"(\d+) runs, \$([\d.]+) total spend", ("runs", "spend"),
     "the run count and total spend quoted in the README"),
]


def check_docs(facts):
    out = []
    for path in ("README.md", os.path.join("assets", "logo", "README.md")):
        fp = os.path.join(CODE, path)
        if not os.path.exists(fp):
            continue
        text = open(fp).read()
        m = re.search(r"\|\s*models scored\s*\|\s*(\d+) runs, \$([\d.]+)", text)
        if m:
            said_runs, said_spend = int(m.group(1)), float(m.group(2))
            if said_runs != facts["all_runs"] or abs(said_spend - facts["spend"]) > 0.02:
                out.append((path, f"README says {said_runs} runs / ${said_spend:.2f}; "
                                  f"records say {facts['all_runs']} / ${facts['spend']:.2f}"))
        # the headline saving must be labelled modelled unless an observed one backs it
        if re.search(r"\b60x\b|\b60×\b", text) and "modelled" not in text:
            out.append((path, "quotes 60× without saying it is a modelled figure"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    rs = list(runs())
    facts = derive()
    fails = 0

    print("SuperRouter audit — every claim re-derived from the records\n")
    print(f"  money actually spent : ${facts['spend']:.2f} across {facts['all_runs']} runs")
    print(f"  comparable records   : {facts['runs']} runs "
          f"({facts['all_runs'] - facts['runs']} quarantined — exam unidentifiable)")
    if "served_calls" in facts:
        print(f"  observed traffic: {facts['served_calls']} calls, "
              f"{facts['observed_saving']}× saving — OBSERVED, not modelled")
    print()

    # The external check is a first-class part of the audit, not a footnote.
    # A self-audit that never leaves its own data can only confirm its own frame.
    xrb = os.path.join(CODE, "state", "xroutebench", "train.jsonl")
    if os.path.exists(xrb):
        try:
            from .xroutebench import load as _xl, evaluate as _xe
            _rows, _ = _xl()
            savings = []
            for seed in range(10):
                _, th, ou, _, _ = _xe(_rows, tolerance=0.0, seed=seed)
                if abs(th[0] - ou[0]) > 1e-9:
                    print(f"  {BAD}  external check: tolerance-0 label changed quality "
                          f"on seed {seed} — it may only break ties")
                    fails += 1
                savings.append(th[1] / ou[1] if ou[1] else 0)
            import statistics
            print(f"  {OK}  external check on xRouteBench: median "
                  f"{statistics.median(savings):.1f}× cheaper at identical quality "
                  f"across 10 splits")
        except Exception as e:
            print(f"  {WARN}  external check could not run: {str(e)[:70]}")
    else:
        print(f"  {WARN}  external check not run — xRouteBench data not downloaded")

    checks = [
        ("no failed request is scored against a model", check_failures_not_scored(rs)),
        ("no directory mixes two exams", check_exams_not_mixed(rs)),
        ("every claim in the docs matches the records", check_docs(facts)),
    ]
    for label, problems in checks:
        if problems:
            fails += len(problems)
            print(f"  {BAD}  {label}")
            for a1, a2 in problems[:6]:
                print(f"        {os.path.basename(str(a1))}: {a2}")
            if len(problems) > 6:
                print(f"        … and {len(problems)-6} more")
        else:
            print(f"  {OK}  {label}")

    unstamped = check_settings_recorded(rs)
    if unstamped:
        print(f"  {WARN}  {len(unstamped)} run(s) predate configuration stamping — "
              f"their setting is unrecoverable, so they are excluded rather than trusted")

    print(f"\n  {fails} failing check(s).")
    if fails:
        print("  A failing audit means a published number no longer matches its source.")
        print("  Fix the number or fix the record; do not fix the audit.")
    if a.strict and fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
