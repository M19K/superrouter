# SuperRouter

**Route each task to the cheapest model that still does it correctly — and prove the "correctly" with a number.**

*Every router picks a model. SuperRouter is the one that measured what the pick costs you.*

Measured on a real workload, not a benchmark:

```
a 100-step QA run, 70% judging / 30% pointing
   all on the reference model : $0.2488
   routed per sub-task        : $0.0041
   60× cheaper, with no measurable quality loss on either sub-task
```

---

## The problem this solves

The open-source routing field is strong and getting stronger. Every serious
router — vLLM Semantic Router, LLMRouter, Plano, Switchyard — solves the hard
plumbing: protocol translation, failover, load balancing, latency-aware
selection.

**None of them measures quality.** They take it as a number you declare.

> *"Quality scoring depends on `quality_score` being configured per model.
> Models without it contribute zero to the quality signal."*
> — vLLM Semantic Router, `multi_factor` docs, under **Known Limitations**

Latency it observes. Cost it reads from pricing. Quality you type into a YAML
file. LLMRouter, the strongest research base available, reduces quality to a
single float per (query, model). Both are reasonable engineering. Neither can
tell you what you give up by going cheaper, because neither ever measured it.

**This measures it.** It is the layer underneath a router, not another router.

## Three things it does differently

### 1 · Quality is resolved by failure mode, never collapsed to one number

A single score cannot separate a model that *misses* problems from one that
*invents* them. Measured here, on the same 140 cases:

| model | catches real defects | flags healthy screens | accuracy |
|---|---|---|---|
| `google/gemma-3-12b-it` | 83% | 21% | 81% |
| `google/gemini-2.5-flash-lite` | 58% | 9% | 77% |

Four points apart on accuracy, and opposites. One number calls them
interchangeable. For any real job they are not.

### 2 · Ground truth is generated, so labelling is nearly free

Labelling is the reason nobody measures quality per task. Two ways around it,
both used here:

- **Plant the defect.** Take a working product, break it in a way you chose, and
  the right answer needs no labeller — you planted it. The set is declared as
  `states × mutations`: 5 real screens × 18 defect classes → 53 planted defects
  and 140 balanced cases, from one file.
- **Ask the runtime.** For "where is this element", the browser already knows.
  Read the rectangle at the instant the screenshot is taken and the ground truth
  is exact.

**Adding a defect class is one dict entry, and it multiplies across every screen
it applies to.**

### 3 · Decisions are made on the confidence bound, never the point estimate

This is the one that changes answers, and it did. Scored on **6** planted
defects, `meta-llama/llama-4-scout` caught 6/6 and was the outright winner. On
**53**, it catches 55% and is second-from-bottom.

**A small golden set is not a noisy version of the right answer. It is a
different answer.** So a model qualifies only when the *lower* bound of its
measured catch rate clears the requirement and the *upper* bound of its false
alarms stays under the ceiling. Unproven is treated as not qualifying.

## Quality does not transfer between task types

Same eight models, three task types, **three different orderings**:

| model | judges screenshots | checks text against its source | points at things |
|---|---|---|---|
| `google/gemma-3-12b-it` | **83%** — best value | **80%** — worst on the ladder | 6% — effectively blind |
| `qwen/qwen3.7-flash` | 72% | **100%** | 11% |
| `amazon/nova-lite-v1` | 47% | 95% | **62%** |
| `anthropic/claude-haiku-4.5` | 77% | 100% | 72% |
| `anthropic/claude-sonnet-5` | 91% | 98% | 76% |

**A model's measured quality on one job carries no information about another
job.** Any router holding one quality number per model is wrong by construction —
not slightly, but in a way that inverts the ranking.

The third task type is text, not vision, and it runs through the same scorer on
the same two axes. That is the generalisation claim, and it is why this is a way
of defining quality rather than vision tooling.

## The finding that shapes the router

**Judging a screen and pointing at it are close to unrelated abilities.** Same
eight models, two golden sets:

| model | catches defects | hits pointing targets |
|---|---|---|
| `google/gemma-3-12b-it` | **83%** — best value, 149× cheaper than reference | **6%** — hits the *wrong* control 3× more often than the right one |
| `mistralai/mistral-small-3.2` | 43% | 6% |
| `qwen/qwen3.7-flash` | 72% | 11% |
| `amazon/nova-lite-v1` | 47% | **62%** |
| `anthropic/claude-haiku-4.5` | 77% | 72% |
| `anthropic/claude-sonnet-5` | 91% | 76% (reference) |

**Routing all of "QA" on the judging measurement builds an agent that describes
a screen perfectly and clicks at random.** So the router routes *sub-tasks*, and
how fine the task label has to be is itself a measurement rather than a taste.

A pointing miss also has two kinds, and one hit rate averages them together:
landing on empty space does nothing, landing inside a **different** clickable
element *clicks it* — the run walks off down a path nobody intended and the
report it produces is fiction.

## Use it

```bash
python3 -m superrouter.pool --vision            # index the live pool, never a remembered price
python3 -m superrouter.evals --dry-run          # cost a run before spending anything
python3 -m superrouter.evals --model <id>       # score judging
python3 -m superrouter.pointing --model <id>    # score pointing
python3 -m superrouter.curve                    # cost against quality, every model
python3 -m superrouter.route_table              # the routing decision, per sub-task
python3 -m superrouter.policy --like anthropic/claude-sonnet-5
python3 -m superrouter.policy --min-catch 70 --max-false-alarm 15
```

Rebuild the golden sets against your own product:

```bash
python3 golden/qa-vision/build.py   --origin http://localhost:8934
python3 golden/qa-point/build.py    --origin http://localhost:8934
python3 golden/text-faithful/build.py --vault ~/your-docs
```

Set `OPENROUTER_API_KEY`, or put one in a gitignored `secrets.json`.

## Layout

| Path | What it is |
|---|---|
| `golden/qa-vision/spec.py` | the judging set as a declaration: states × defect classes |
| `golden/qa-vision/build.py` | renders it to frames + manifest, gated on pixel change |
| `golden/qa-point/build.py` | the pointing set: targets with exact rectangles from the DOM |
| `golden/text-faithful/spec.py` | the text set: 11 corruption classes, easy and hard |
| `golden/text-faithful/build.py` | verbatim passages from real documents, one planted falsehood each |
| `supersuperrouter/pool.py` | indexes the live OpenRouter pool |
| `supersuperrouter/evals.py` | scores judging — catch, false alarms, refusals, intervals |
| `supersuperrouter/pointing.py` | scores pointing — hit, wrong control, empty space |
| `supersuperrouter/curve.py` | cost against quality |
| `supersuperrouter/policy.py` | non-inferiority test against a reference |
| `supersuperrouter/route_table.py` | the per-sub-task routing table |
| `supersuperrouter/export_llmrouter.py` | hands it all to LLMRouter |
| `state/` | pool snapshot, every scored run, exported training data |

## Integrity rules the instrument enforces

- **A planted defect must move pixels.** One of 18 classes returned success and
  changed nothing — a selector that matched no element. Every fixture is gated
  against the healthy frame of the same screen.
- **Refusing to answer is not a wrong answer.** One model returns no usable
  verdict on 24% of cases. Scored as wrong, that hides a disqualifying failure:
  it cannot drive the run at all.
- **Frames are frozen.** The source page renders differently on every load, so
  scoring live would confuse a worse model with a changed page.
- **Coordinate convention is calibrated once per model over a whole run**, never
  per case — per-case repair hands the model two guesses.
- **Many survivors mean one of two opposite things, and the tooling tells them
  apart.** Either several models are genuinely tied at the top — then picking
  the cheapest is exactly right — or the set is too easy or too small to
  separate them, and the pick is weakly evidenced. The difference is whether the
  survivors actually score well, not how many there are.
- **Price is not cost.** One 1280×800 screenshot billed 1,857 input tokens where
  two documented estimation methods predicted 1,020 and 1,150. Cost per task is
  read back from the provider, never estimated.

## The contribution to LLMRouter: fix the label, not the algorithm

Their trainer selects what to learn with one line:

```python
routing_data_train.loc[routing_data_train.groupby("query")["performance"].idxmax()]
```

**`performance` is a single float, and with a binary correctness metric it is
almost always tied.** Measured on this corpus, *every* query has several models
at 1.0, so `idxmax` breaks the tie by row order — the router is trained to
predict whichever correct model happened to be written first. **Cost never
enters the label**, so no algorithm trained on it can save money except by
accident.

The fix is one field, and it leaves all 16 algorithms untouched:

```python
performance = 0                     if wrong
            = 1 − 0.5 · cost_rank   if right      # cheapest correct → 1.0
```

Correctness still dominates — every right answer outranks every wrong one — but
`argmax` now means *the cheapest model that got it right*, which is the routing
objective.

**Measured, held-out split, their own trained KNN router:**

| accuracy | cost | vs dearest | strategy |
|---|---|---|---|
| 88% | $0.10408 | 1× | always `claude-sonnet-5` |
| 76% | $0.00068 | 152× | always `gemma-3-12b-it` |
| 71% | $0.03125 | 3× | trained KNN, **their** label |
| **79%** | **$0.00142** | **73×** | trained KNN, **cost-aware** label |

Same algorithm, same embeddings, same held-out cases, one field changed: **22×
cheaper and 8 points more accurate.**

**Being straight about what this does not show.** Neither trained router beats
always-`sonnet-5` on accuracy — 79% against 88%. KNN is the simplest of their 16
and it is learning from 98 training cases where their own corpora use thousands.
The label change is the result here; the trained router is a working
demonstration, not yet a better router.

### A second, smaller fix: multimodal queries need to name their input

They group by the query **string**. For a vision task the same sentence is asked
of many screenshots and the right answer differs per screenshot — grouping by
text alone merged our 140 cases into **25** groups and discarded 115 labels. The
query therefore carries its frame: `[hub-dark] every section label is legible…`.
General to any multimodal routing corpus.

## Relationship to LLMRouter

[`ulab-uiuc/LLMRouter`](https://github.com/ulab-uiuc/LLMRouter) (MIT) ships 16
routing algorithms, a training CLI and a benchmark. That is the engine, and it
is better than we would write. What it cannot supply is labelled data for *your*
task — its pipeline is built around eleven public benchmarks.

`supersuperrouter/export_llmrouter.py` emits our measurements in its native record format,
plus the task and metric registration it needs. Resolved per-failure-mode quality
is carried alongside, because their `performance` float cannot hold it.

No LLMRouter code is vendored here.

## Status

Working instrument. Two task types measured, results reproducible from `state/`.
LLMRouter trains on the exported corpus end to end — trained models and their
configs are in `state/llmrouter_corpus/`. Not yet a router: the routing table is
produced, not yet served.

## Licence

Not yet licensed. Intended for release under MIT or Apache-2.0 once the protocol
is finished. Until then, all rights reserved.
