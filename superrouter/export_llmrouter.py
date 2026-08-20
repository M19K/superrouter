#!/usr/bin/env python3
"""
export_llmrouter.py — hand our measurements to LLMRouter as training data.

    python3 -m superrouter.export_llmrouter          # writes state/llmrouter/

**Why this file is short, and why that is the point.** LLMRouter (ulab-uiuc,
MIT) already ships 16 routing algorithms, a training CLI and a benchmark. It is
a far better routing engine than we would write. What it cannot supply is
labelled data for *your* task — its pipeline is built around eleven public
benchmarks. That labelling is precisely what mutation-generated golden sets
produce cheaply, so the two halves fit without an adapter:

    their record: task_name · query · ground_truth · metric · model_name
                  · response · performance · token_num
    our run:      task type · assertion · correct answer · model · what it said
                  · was it right · tokens

**And the one place we do not fit, which is the differentiation.** Their
`performance` is a single float — `@evaluation_metric` decorates a function
returning one number per (query, model). One number cannot separate a model that
misses defects from one that invents them; measured here, two models four points
apart on accuracy were opposites on that axis. So we emit `performance` for
compatibility and carry the resolved quality alongside it in `quality_axes`,
where a router that wants it can read it and their trainers can ignore it.
"""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
RUNS = os.path.join(CODE, "state", "runs")
POINT_RUNS = os.path.join(CODE, "state", "point_runs")
OUT = os.path.join(CODE, "state", "llmrouter")

CUSTOM_METRIC = '''"""Register our task and its metric with LLMRouter.

    import custom_tasks.qa_vision_assert   # triggers registration

`qa_vision_assert` is theirs-compatible: one float, exact-match on TRUE/FALSE.
The resolved quality that the routing decision should actually use lives in
`quality_axes` on each record, because a float cannot carry it.
"""
from llmrouter.evaluation import evaluation_metric
from llmrouter.utils.prompting import register_prompt


@register_prompt('qa_vision_assert', default_metric='qa_vision_assert')
def format_qa_vision_prompt(sample_data):
    return {
        "system": "You are checking a screenshot of a web page during a QA pass. "
                  "Answer with exactly one word: TRUE or FALSE.",
        "user": f"Statement: {sample_data.get('query', '')}",
    }


@evaluation_metric('qa_vision_assert')
def qa_vision_assert(prediction: str, ground_truth: str, **kwargs) -> float:
    head = prediction.strip().upper().replace("*", "").replace("`", "").lstrip("# ")
    if head.startswith("TRUE"):
        said = "TRUE"
    elif head.startswith("FALSE"):
        said = "FALSE"
    else:
        return 0.0          # no answer is a wrong answer, never a dropped one
    return 1.0 if said == ground_truth.strip().upper() else 0.0
'''


def main():
    os.makedirs(OUT, exist_ok=True)
    # Newest complete run per model only. Re-running a ladder must not double
    # the training set — that silently doubles a model's weight.
    latest = {}
    for path in sorted(glob.glob(os.path.join(RUNS, "*.json"))):
        blob = json.load(open(path))
        if blob["summary"]["cases"] < 100:
            continue
        latest[blob["summary"]["model"]] = blob

    rows, models = [], {}
    for blob in latest.values():
        s = blob["summary"]
        models[s["model"]] = s
        for r in blob["results"]:
            rows.append({
                "task_name": "qa_vision_assert",
                "query": r["assert"],
                "ground_truth": "TRUE" if r["answer"] else "FALSE",
                "metric": "qa_vision_assert",
                "model_name": s["model"],
                "response": r.get("raw", ""),
                "performance": 1.0 if r["correct"] else 0.0,
                "token_num": (r.get("in_tokens") or 0) + (r.get("out_tokens") or 0),
                # ours, and the reason this project exists
                "frame": r["frame"],
                "defect_class": r.get("defect"),
                "needs_defect_sight": r["needs_defect_sight"],
                "cost_usd": r["cost"],
                "seconds": r["seconds"],
            })

    data = os.path.join(OUT, "routing_data.jsonl")
    with open(data, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    axes = {m: {"catch": s["catch"], "catch_ci": s["catch_ci"],
                "false_alarm": s["false_alarm"], "false_alarm_ci": s["false_alarm_ci"],
                "accuracy": s["accuracy"], "cost_usd_per_run": s["cost_usd"],
                "seconds_per_case": s["seconds_per_case"],
                "defect_classes_missed_entirely": s.get("defect_classes_missed_entirely", [])}
            for m, s in models.items()}
    with open(os.path.join(OUT, "quality_axes.json"), "w") as f:
        json.dump({"task_name": "qa_vision_assert",
                   "note": "resolved quality per failure mode, with 95% intervals. "
                           "LLMRouter's `performance` float is the projection of this "
                           "onto one axis, and the projection is lossy.",
                   "models": axes}, f, indent=1)

    ct = os.path.join(OUT, "custom_tasks")
    os.makedirs(ct, exist_ok=True)
    with open(os.path.join(ct, "qa_vision_assert.py"), "w") as f:
        f.write(CUSTOM_METRIC)

    # the pointing task, same format, different metric
    prows = []
    plate = {}
    for path in sorted(glob.glob(os.path.join(POINT_RUNS, "*.json"))):
        blob = json.load(open(path))
        if blob["summary"]["cases"] >= 100:
            plate[blob["summary"]["model"]] = blob
    for blob in plate.values():
        m = blob["summary"]["model"]
        for r in blob["results"]:
            b = r["box"]
            prows.append({
                "task_name": "qa_vision_point",
                "query": r["target"],
                "ground_truth": f"{b['x']},{b['y']},{b['w']},{b['h']}",
                "metric": "qa_vision_point",
                "model_name": m,
                "response": str(r.get("said")),
                "performance": 1.0 if r["outcome"] == "hit" else 0.0,
                "token_num": 0,
                "frame": r["frame"],
                "outcome": r["outcome"],
                "convention": r.get("convention"),
                "distance_px": r.get("distance_px"),
                "cost_usd": r["cost"], "seconds": r["seconds"],
            })
    if prows:
        with open(os.path.join(OUT, "routing_data_point.jsonl"), "w") as f:
            for r in prows:
                f.write(json.dumps(r) + "\n")
        for m, blob in plate.items():
            s2 = blob["summary"]
            axes.setdefault(m, {})["point"] = {
                "hit": s2["hit"], "hit_ci": s2["hit_ci"],
                "wrong_control": s2["wrong_thing"], "wrong_control_ci": s2["wrong_thing_ci"],
                "cost_usd_per_run": s2["cost_usd"], "convention": s2["convention"]}
        with open(os.path.join(OUT, "quality_axes.json"), "w") as f:
            json.dump({"task_name": ["qa_vision_assert", "qa_vision_point"],
                       "note": "resolved quality per failure mode, with 95% intervals. "
                               "LLMRouter's `performance` float is the projection of this "
                               "onto one axis, and the projection is lossy.",
                       "models": axes}, f, indent=1)
        print(f"{len(prows)} pointing records over {len(plate)} models → "
              f"{OUT}/routing_data_point.jsonl")

    print(f"{len(rows)} routing records over {len(models)} models → {data}")
    print(f"resolved quality per model            → {OUT}/quality_axes.json")
    print(f"LLMRouter task + metric registration  → {ct}/qa_vision_assert.py")
    print("\ntrain a router on it:")
    print("  llmrouter train --router mfrouter --data state/llmrouter/routing_data.jsonl")


if __name__ == "__main__":
    main()
