#!/usr/bin/env python3
"""
pointing.py — can the model point at the right thing? The harder half of QA.

    python3 -m superrouter.pointing --model google/gemini-2.5-flash-lite
    python3 -m superrouter.pointing --model a/b --model c/d --workers 10

**Judging and locating are different abilities.** The assert set measures whether
a model can say what is on a screen. This measures whether it can act on it —
which is what `aiTap` and `aiInput` need, and what a QA agent that has to drive
rather than watch stands or falls on.

**Three outcomes, not two, and the third is why one hit rate is not enough.**

    hit          the point lands inside the thing it was asked for
    wrong thing  it lands inside a DIFFERENT clickable element
    empty space  it lands on nothing clickable

The last two are both misses and they are not the same failure. Missing into
empty space does nothing. Missing into another control **clicks it** — the QA
run carries on down a path nobody intended, and the report it produces is
fiction. A single hit rate averages a harmless failure together with a
destructive one, which is the same mistake as scoring assert-quality with a
single accuracy number.

**Coordinate convention is calibrated per model, once, from the whole run.**
Some models answer in a normalised 0–1000 space no matter what they were asked.
Scoring that raw blames capability for what is a formatting habit — a real
harness knows its adapters and converts. But repairing it *per case*, taking
whichever reading happens to hit, would be cheating: it hands the model two
guesses.

So the convention is chosen **once per model per run**, by whichever reading
scores better across every case, and applied to all of them. It is a property of
the model, measured. The chosen convention and both raw scores are always
printed, so the calibration is visible rather than buried.
"""
import argparse
import concurrent.futures as futures
import json
import os
import re
import time

from .evals import TRANSPORT_FAULTS, ask, key, wilson

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(CODE, "golden", "qa-point")
RUNS = os.path.join(CODE, "state", "point_runs")

PROMPT = (
    "This is a screenshot of a web page, {vw} pixels wide and {vh} pixels tall.\n\n"
    "Find: {target}\n\n"
    "Reply with the pixel coordinates of the point you would click, as two "
    "numbers separated by a comma, and nothing else. The first number is the "
    "distance from the left edge, the second from the top edge. "
    "For example: 640,400"
)

NUM = re.compile(r"-?\d+(?:\.\d+)?")


def read_point(text):
    """First two numbers wins. Models wrap coordinates in every shape there is —
    JSON, prose, brackets — and the numbers are the answer in all of them."""
    n = NUM.findall(text or "")
    if len(n) < 2:
        return None
    return float(n[0]), float(n[1])


def inside(pt, b, pad=0):
    x, y = pt
    return (b["x"] - pad <= x <= b["x"] + b["w"] + pad
            and b["y"] - pad <= y <= b["y"] + b["h"] + pad)


def classify(pt, case):
    if pt is None:
        return "refused"
    if inside(pt, case["box"]):
        return "hit"
    for r in case["clickables"]:
        if inside(pt, r):
            return "wrong-thing"
    return "empty-space"


def frame_b64(name):
    import base64
    with open(os.path.join(GOLDEN, "frames", f"{name}.png"), "rb") as f:
        return base64.b64encode(f.read()).decode()


def as_absolute(pt, c, convention):
    if pt is None or convention == "absolute":
        return pt
    return pt[0] / 1000 * c["vw"], pt[1] / 1000 * c["vh"]


def score(model, cases, api_key, workers=8, verbose=False):
    cache = {c["frame"]: frame_b64(c["frame"]) for c in cases}

    def one(ic):
        i, c = ic
        prompt = PROMPT.format(target=c["target"], vw=c["vw"], vh=c["vh"])
        try:
            r = ask(model, prompt, cache[c["frame"]], api_key)
        except TRANSPORT_FAULTS as e:
            return i, {**c, "outcome": "error", "error": str(e)[:120],
                       "cost": 0.0, "seconds": 0.0}
        pt = read_point(r["text"])
        return i, {**c, "said": pt, "raw": r["text"][:60],
                   "cost": r["cost"], "seconds": r["seconds"]}

    out = [None] * len(cases)
    with futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, res in ex.map(one, enumerate(cases)):
            out[i] = res

    # calibrate the convention once, over the whole run, then commit to it
    tally = {conv: sum(1 for r in out
                       if classify(as_absolute(r.get("said"), r, conv), r) == "hit")
             for conv in ("absolute", "normalised")}
    convention = max(tally, key=tally.get)
    for r in out:
        pt = as_absolute(r.get("said"), r, convention)
        r["point_used"] = pt
        r["convention"] = convention
        r["outcome"] = "error" if r.get("error") else classify(pt, r)
        if pt:
            cx = r["box"]["x"] + r["box"]["w"] / 2
            cy = r["box"]["y"] + r["box"]["h"] / 2
            r["distance_px"] = round(((pt[0] - cx) ** 2 + (pt[1] - cy) ** 2) ** 0.5)
        else:
            r["distance_px"] = None
    out[0]["_tally"] = tally
    if verbose:
        for r in out:
            if r["outcome"] != "hit":
                print(f"  {r['outcome']:<12} {r['id']} [{r['frame']}] {r['target'][:44]}"
                      f"  said {r.get('said')}  {r.get('distance_px')}px off")
    return out


def summarise(model, res):
    n = len(res)
    c = {k: sum(1 for r in res if r["outcome"] == k)
         for k in ("hit", "wrong-thing", "empty-space", "refused", "error")}
    hits = [r["distance_px"] for r in res if r["outcome"] == "hit"]
    misses = sorted(r["distance_px"] for r in res
                    if r["outcome"] in ("wrong-thing", "empty-space") and r["distance_px"] is not None)
    tally = res[0].get("_tally", {})
    return {
        "model": model, "cases": n,
        "hit": round(100 * c["hit"] / n), "hit_ci": wilson(c["hit"], n),
        "hit_n": f"{c['hit']}/{n}",
        "wrong_thing": round(100 * c["wrong-thing"] / n),
        "wrong_thing_ci": wilson(c["wrong-thing"], n),
        "wrong_thing_n": f"{c['wrong-thing']}/{n}",
        "empty_space": round(100 * c["empty-space"] / n),
        "refused": round(100 * (c["refused"] + c["error"]) / n),
        "median_miss_px": misses[len(misses) // 2] if misses else None,
        "convention": res[0].get("convention", "absolute"),
        "hits_absolute": tally.get("absolute"),
        "hits_normalised": tally.get("normalised"),
        "cost_usd": round(sum(r["cost"] for r in res), 6),
        "seconds_per_case": round(sum(r["seconds"] for r in res) / n, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", default=[])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    g = json.load(open(os.path.join(GOLDEN, "manifest.json")))
    cases = g["case_list"][: a.limit] if a.limit else g["case_list"]
    print(f"pointing set · {len(cases)} targets with exact rectangles · "
          f"{len({c['frame'] for c in cases})} screens\n")
    if not a.model:
        raise SystemExit("give at least one --model")

    api_key = key()
    os.makedirs(RUNS, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
    rows = []
    for m in a.model:
        print(f"scoring {m} …")
        res = score(m, cases, api_key, workers=a.workers, verbose=a.verbose)
        s = summarise(m, res)
        rows.append(s)
        with open(os.path.join(RUNS, f"{stamp}_{m.replace('/', '_')}.json"), "w") as f:
            json.dump({"summary": s, "results": res}, f, indent=1)
        print(f"  hit {s['hit']}% ({s['hit_n']}, {s['hit_ci'][0]}-{s['hit_ci'][1]})  "
              f"clicked the WRONG control {s['wrong_thing']}% ({s['wrong_thing_n']})  "
              f"empty space {s['empty_space']}%  ${s['cost_usd']:.5f}  "
              f"[answers in {s['convention']} coordinates: "
              f"{s['hits_absolute']} vs {s['hits_normalised']} hits]\n")

    print(f"{'hit (95% CI)':>18} {'wrong control':>14} {'empty':>7} {'miss px':>8} "
          f"{'$ / run':>9}  model")
    for s in sorted(rows, key=lambda s: -s["hit"]):
        print(f"{s['hit']:>10}% ({s['hit_ci'][0]:>2}-{s['hit_ci'][1]:<2}) "
              f"{s['wrong_thing']:>13}% {s['empty_space']:>6}% "
              f"{str(s['median_miss_px'] or '-'):>8} {s['cost_usd']:9.5f}  {s['model']}")
    print("\ncoordinate convention, calibrated once per model over the whole run:")
    for s in rows:
        print(f"  {s['model']:<46} {s['convention']:<11} "
              f"(absolute {s['hits_absolute']} hits, normalised {s['hits_normalised']})")
    print(f"\nrun records → {RUNS}")


if __name__ == "__main__":
    main()
