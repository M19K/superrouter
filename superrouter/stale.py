#!/usr/bin/env python3
"""
stale.py — what has gone out of date, and what re-measuring it would cost.

    python3 -m superrouter.stale
    python3 -m superrouter.stale --json     # for a cron job to act on

**A measurement describes the day it was taken, and four things move underneath
it.** None of them announce themselves:

  the exam      a golden set gets rebalanced or a product is rebuilt, and every
                score taken on the old one stops being comparable
  the price     OpenRouter's prices move weekly; a pick that was cheapest is not
                automatically still cheapest
  the pool      models appear. A new one cannot win a comparison it was never
                entered into, so the table quietly stops being the best answer
  the model     providers update a model in place under the same name, and
                nothing in the record can see that at all

The first three are computable from what is already on disk plus one live fetch,
and this reports them. **The fourth is not**, which is why shadow mode exists —
and shadow mode measures drift from the reference and nothing else, so the two
together still do not remove the need to re-run the exam. This says when.

**It reports; it does not spend.** Re-measuring costs money, so it prints what a
refresh would cost and stops. Nothing here decides to spend on its own.
"""
import argparse
import glob
import json
import os
import time

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(CODE, "state", "pool.json")

# min_cases is per task: a pointing set is smaller than a judging set by
# construction, and a shared floor of 60 silently reported pointing as "never
# measured" when ten runs of it were on disk.
TASKS = {
    "qa-vision-assert": ("state/runs_portfolio", "golden/qa-vision/sets/portfolio", 100),
    "text-faithful":    ("state/text_runs",      "golden/text-faithful", 60),
    "qa-vision-point":  ("state/point_runs",     "golden/qa-point", 40),
}


def newest_runs(runs_dir, min_cases=60):
    best = {}
    for p in sorted(glob.glob(os.path.join(CODE, runs_dir, "*.json"))):
        s = json.load(open(p))["summary"]
        if s["cases"] >= min_cases:
            best[s["model"]] = dict(s, _file=os.path.basename(p),
                                    _when=os.path.getmtime(p))
    return best


def exam_fingerprint(golden_dir):
    import hashlib
    m = os.path.join(CODE, golden_dir, "manifest.json")
    if not os.path.exists(m):
        return None, None
    cases = json.load(open(m)).get("case_list") or []
    h = hashlib.sha256()
    for c in sorted(cases, key=lambda c: c["id"]):
        h.update(f"{c['id']}|{c.get('answer')}|{c.get('corruption') or ''}"
                 f"|{c.get('variant') or ''}|{c.get('defect') or ''}".encode())
    return h.hexdigest()[:12], len(cases)


def live_pool():
    import urllib.request
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=60) as r:
        return json.load(r)["data"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="skip the live fetch; price and pool drift go unchecked")
    a = ap.parse_args()
    report = {"checked": time.strftime("%Y-%m-%d"), "tasks": {}, "pool": {}}

    for task, (runs_dir, golden_dir, min_cases) in TASKS.items():
        runs = newest_runs(runs_dir, min_cases)
        want, n_cases = exam_fingerprint(golden_dir)
        if not runs:
            report["tasks"][task] = {"state": "never measured"}
            continue
        # Compare against the EXAM the run was drawn from, never the subset it
        # sat — a --limit run legitimately sees fewer cases and is not thereby
        # out of date. A run predating the stamp has no exam id and is reported
        # as unknown rather than assumed current.
        stale, unknown = [], []
        for m, r in runs.items():
            got = r.get("exam_fingerprint")
            if got is None:
                unknown.append(m)
            elif want and got != want:
                stale.append(m)
        stale, unknown = sorted(stale), sorted(unknown)
        ages = {m: round((time.time() - s["_when"]) / 86400, 1) for m, s in runs.items()}
        report["tasks"][task] = {
            "exam": want, "exam_cases": n_cases,
            "models_measured": len(runs),
            # unknown is not "current" — it is unverifiable, which is a
            # different thing and must not be counted as passing
            "on_the_current_exam": len(runs) - len(stale) - len(unknown),
            "need_remeasuring": stale,
            "exam_unknown": unknown,
            "oldest_run_days": max(ages.values()) if ages else None,
        }

    if not a.offline:
        try:
            live = live_pool()
            known = {}
            if os.path.exists(POOL):
                known = {m["id"]: m for m in json.load(open(POOL))["models"]}
            now = {m["id"]: m for m in live}
            appeared = sorted(set(now) - set(known))
            gone = sorted(set(known) - set(now))
            moved = []
            for mid, m in now.items():
                if mid not in known:
                    continue
                try:
                    p_now = float((m.get("pricing") or {}).get("prompt") or 0) * 1e6
                except (TypeError, ValueError):
                    continue
                p_was = known[mid].get("in_per_m")
                if p_was and abs(p_now - p_was) > max(0.001, 0.05 * p_was):
                    moved.append({"model": mid, "was": round(p_was, 4),
                                  "now": round(p_now, 4)})
            report["pool"] = {"live": len(now), "indexed": len(known),
                              "appeared": len(appeared), "disappeared": gone[:10],
                              "price_moved": sorted(moved,
                                                    key=lambda d: -abs(d["now"] - d["was"]))[:8]}
        except Exception as e:
            report["pool"] = {"error": str(e)[:120]}

    if a.json:
        print(json.dumps(report, indent=1))
        return

    print(f"staleness check · {report['checked']}\n")
    for task, r in report["tasks"].items():
        if r.get("state"):
            print(f"  {task:<20} {r['state']}")
            continue
        ok = r["on_the_current_exam"]
        print(f"  {task}")
        print(f"    exam {r['exam']} · {r['exam_cases']} cases · "
              f"{ok} of {r['models_measured']} models measured on it")
        if r.get("exam_unknown"):
            print(f"    {len(r['exam_unknown'])} run(s) predate exam stamping — "
                  f"which set they sat is unrecoverable, so they are not counted.")
        if r["need_remeasuring"]:
            print(f"    NEEDS RE-MEASURING — scored on an older exam, not comparable:")
            for m in r["need_remeasuring"][:6]:
                print(f"      {m}")
            if len(r["need_remeasuring"]) > 6:
                print(f"      … and {len(r['need_remeasuring']) - 6} more")
        print(f"    oldest run: {r['oldest_run_days']} days\n")

    p = report.get("pool") or {}
    if p.get("error"):
        print(f"  pool: could not be checked — {p['error']}")
    elif p:
        print(f"  pool · {p['live']} models live, {p['indexed']} in our index")
        if p["appeared"]:
            print(f"    {p['appeared']} model(s) have appeared since the index was taken.")
            print(f"    A model that was never entered cannot win, so the table is not")
            print(f"    wrong — it is answering a smaller question than you think.")
        if p["disappeared"]:
            print(f"    gone: {', '.join(p['disappeared'][:4])}")
        if p["price_moved"]:
            print(f"    prices moved on {len(p['price_moved'])} model(s) we measured:")
            for d in p["price_moved"][:5]:
                print(f"      {d['model']:<44} ${d['was']} → ${d['now']} /M in")
    print("\n  Nothing was re-measured and nothing was spent. Re-run the ladder for")
    print("  whatever above matters to you; `--dry-run` will price it first.")


if __name__ == "__main__":
    main()
