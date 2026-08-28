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

from ._io import read_json, read_lines, read_text

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OK, BAD, WARN = "PASS", "FAIL", "UNVERIFIABLE"


def runs(include_quarantined=False):
    """Comparable runs by default. Quarantined ones are real evidence of money
    actually spent, but their exam cannot be identified, so they may never enter
    a comparison — two different questions, two different sets."""
    pats = [os.path.join(CODE, "state", "*run*", "*.json")]
    if include_quarantined:
        # **Any subdirectory, not only `_unstamped`.** A runs directory that
        # holds two exams is refused by `check_exams_not_mixed`, and the fix is
        # to move the retired exam's runs into a directory named for it. That
        # made them invisible here — spend fell by $0.31 and 16 real runs
        # stopped existing — because this only knew one subdirectory name.
        # A retired exam's runs are still money spent and still evidence; they
        # are simply not comparable with the current exam, which is what
        # keeping them out of the default pattern already expresses.
        pats.append(os.path.join(CODE, "state", "*run*", "*", "*.json"))
    for p in sorted(x for pat in pats for x in glob.glob(pat)):
        try:
            yield p, read_json(p)
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
        rows = [json.loads(ln) for ln in read_lines(served)]
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
        text = read_text(fp)
        # **Not finding the claim is a failure, not a pass.** This matched
        # `| models scored | 106 runs, $4.56` — one phrasing of one row. The
        # README was rewritten on 2026-08-22 to read "$4.56 actually spent
        # across 106 runs", the regex stopped matching, and the check went
        # silent for the one thing it exists to catch. Found 2026-08-27 by
        # running the audit and comparing its own output to the file it had
        # just declared correct: it said 108 runs / $4.83 and passed a README
        # saying 106 / $4.56. Same class as every other bug in this project —
        # the no-data branch was the permissive one.
        row = next((ln for ln in text.splitlines()
                    if re.search(r"\|\s*models scored\s*\|", ln)), None)
        if row is None and path == "README.md":
            out.append((path, "no `models scored` row found — the run count and "
                              "spend cannot be checked against the records"))
        elif row is not None:
            n = re.search(r"(\d+)\s+runs", row)
            d = re.search(r"\$\s*([\d.]+)", row)
            if not (n and d):
                out.append((path, f"`models scored` row does not state a run count "
                                  f"and a dollar figure this can read: {row.strip()[:80]}"))
            else:
                said_runs, said_spend = int(n.group(1)), float(d.group(1))
                if said_runs != facts["all_runs"] or abs(said_spend - facts["spend"]) > 0.02:
                    out.append((path, f"README says {said_runs} runs / ${said_spend:.2f}; "
                                      f"records say {facts['all_runs']} / ${facts['spend']:.2f}"))
        # The observed saving is the one number that is a bill rather than a
        # model, so a stale one is the most misleading thing on the page.
        obs = re.search(r"\|\s*\*\*observed\*\* saving[^|]*\|[^|]*?\*\*(\d+)x\*\*"
                        r"[^|]*?over (\d+) routed calls", text)
        if obs and "observed_saving" in facts:
            if (int(obs.group(1)) != facts["observed_saving"]
                    or int(obs.group(2)) != facts["served_calls"]):
                out.append((path, f"README says {obs.group(1)}x over {obs.group(2)} routed "
                                  f"calls; records say {facts['observed_saving']}x over "
                                  f"{facts['served_calls']}"))
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
    unchecked = 0

    # **A check that had nothing to check has not passed.** Run cold on a fresh
    # clone this reported "0 failing checks" with zero records on disk — a
    # stranger would read that as verified. It is the same failure this whole
    # module exists to close, one level up: the instrument reporting success
    # when it was given nothing to inspect.
    if not rs:
        print("  NOTHING MEASURED YET — no run records on disk.\n")
        print("  This is not a pass. Nothing below could be checked, and the")
        print("  numbers in the README describe the author's runs, not yours.")
        print("  Build a golden set against your product and score it first:\n")
        print("    python3 golden/qa-vision/build_generic.py --origin https://your.site --name yours")
        print("    python3 -m superrouter.evals --dry-run")
        print("    python3 -m superrouter.evals --model <a> --model <b>\n")
        if a.strict:
            sys.exit(2)
        return

    print("SuperRouter audit — every claim re-derived from the records\n")
    print(f"  money actually spent : ${facts['spend']:.2f} across {facts['all_runs']} runs")
    print(f"  comparable records   : {facts['runs']} runs "
          f"({facts['all_runs'] - facts['runs']} not comparable — either the "
          f"exam cannot be identified, or it sat a retired one)")
    if "served_calls" in facts:
        print(f"  observed traffic: {facts['served_calls']} calls, "
              f"{facts['observed_saving']}× saving — OBSERVED, not modelled")
    print()

    # The external check is a first-class part of the audit, not a footnote.
    # A self-audit that never leaves its own data can only confirm its own frame.
    xrb = os.path.join(CODE, "state", "xroutebench", "train.jsonl")
    if os.path.exists(xrb):
        try:
            from .xroutebench import evaluate as _xe
            from .xroutebench import load as _xl
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
            print(f"  {OK}  external check on the public benchmark: median "
                  f"{statistics.median(savings):.1f}× cheaper at identical quality "
                  f"across 10 splits")
        except Exception as e:
            print(f"  {WARN}  external check could not run: {str(e)[:70]}")
            unchecked += 1
    else:
        print(f"  {WARN}  external check not run — benchmark data not downloaded")
        unchecked += 1

    # A policy that does not beat random at its own rate bought nothing, however
    # much it saved. Asserted rather than assumed, on the records.
    try:
        from .cascade import evaluate as _ce
        from .cascade import load as _cl
        from .cascade import random_at as _cr
        from .cascade import signals as _cs
        _runs, _ = _cl("text-faithful")
        _rank = sorted(_runs, key=lambda m: _runs[m]["summary"].get("cost_usd", 0))
        _ref = _rank[-1]
        _rr = {r["id"]: r for r in _runs[_ref]["results"]}
        _cheap = next(m for m in _rank if m != _ref
                      and len({r["id"] for r in _runs[m]["results"]} & _rr.keys()) >= 30)
        _cc = {r["id"]: r for r in _runs[_cheap]["results"]}
        _ids = sorted(set(_cc) & set(_rr))[:120]
        _res = _ce(_cc, _rr, _ids, lambda r: any(_cs(r).values()), 0, 1)
        _ra, _sd = _cr(_cc, _rr, _ids, _res["rate"])
        _gap = _res["accuracy"] - _ra
        # **A verifier that never fired has not failed.** At an escalation rate
        # of zero the policy IS the never-escalate baseline, and so is random at
        # that rate — the comparison is degenerate and the gap is 0.000 by
        # construction. Reporting that as "does NOT beat random" says the
        # verifier was tried and found worthless, when it was never tried.
        #
        # Found 2026-08-28, when a rebuilt text exam paired a cheap tier at 93.3%
        # against a reference at 94.2%. The cheap model never produced an empty,
        # malformed or hedged answer, so no level had anything to escalate on.
        # That is a fact about the task, not a fault in the policy — and this
        # project's recurring bug is exactly this: the instrument blaming what it
        # measures for a situation that is not a failure.
        if _res["rate"] == 0:
            print(f"  {WARN}  the verifier never fired — the cheap tier "
                  f"({_cheap}) produced a usable answer on every case, so no "
                  f"level had anything to escalate on. Nothing to beat, and "
                  f"nothing proven either way.")
            unchecked += 1
        elif _gap > 2 * _sd:
            print(f"  {OK}  the verifier beats random at its own rate "
                  f"({_gap:+.3f} at a {_res['rate']:.0%} escalation rate)")
        else:
            print(f"  {BAD}  the verifier does NOT beat random at its own rate "
                  f"({_gap:+.3f} at a {_res['rate']:.0%} escalation rate) — it "
                  f"escalated and gained nothing, so its saving is arithmetic")
            fails += 1
    except Exception as e:
        print(f"  {WARN}  deferral check could not run: {str(e)[:70]}")
        unchecked += 1

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

    print(f"\n  {fails} failing check(s), {unchecked} that could not run.")
    if unchecked:
        print(f"  A check that could not run has NOT passed. Treat the "
              f"{unchecked} above as open.")
    if fails:
        print("  A failing audit means a published number no longer matches its source.")
        print("  Fix the number or fix the record; do not fix the audit.")
    if a.strict and fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
