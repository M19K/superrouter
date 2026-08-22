# SuperRouter

**Route each task to the cheapest model that still does it correctly — and prove the "correctly" with a number.**

*Every router picks a model. SuperRouter is the one that measured what the pick costs you.*

Measured on a real workload, not a benchmark:

```
a 100-step QA run, 70% judging / 30% pointing
   all on the reference model : $0.2488
   routed per sub-task        : $0.0041
   from: judging 140 cases · pointing 108 cases, one product, one exam each
   60× cheaper, with no measurable quality loss on either sub-task
```

*The tool prints that provenance line itself. A headline number that cannot say
which product, which exam and how many cases it came from is exactly what this
project objects to in every other router.*

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

### 4 · The level belongs to your product, so it is never shipped

Both task types, two products, one generic generator:

| | rank correlation | median level shift |
|---|---|---|
| judging | 0.83 | **−22 points** — every model worse on product B |
| pointing | 0.94 | **+13 points** — most models *better* on product B |

Judging alone reads as *models degrade on unseen products*. Pointing refutes
that — the shift went the other way. **The level is a property of the product**;
a docs site with large obvious navigation is easier to point at than an
unconventional layout.

So order transfers, and a published leaderboard is a fair guess at it. The level
does not transfer in either direction, and the level is the only thing that
answers *is this good enough for me*.

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
python3 -m superrouter.stale               # what has gone out of date, and why
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

## Shadow mode — and the honest limit on what it proves

`--shadow N` sends one call in every N to the reference as well, off the response
path, and records whether the two agreed. It is how a measurement taken on one
day survives models being updated under the same name and traffic drifting away
from the exam.

**Measured against ground truth on 60 live calls, because agreement is easy to
misread:**

| | |
|---|---|
| routed model agreed with the reference | **100%** |
| routed model correct against ground truth | **75%** |
| agreed *and both wrong* | **12 of 50 — 24% of traffic** |

**Agreement measures drift from the reference and nothing else.** It goes blind
exactly where the two models share a blind spot, and a shared blind spot is the
normal case. So shadow mode tells you *when* to re-run the golden set. It never
replaces it, and a high agreement rate is not evidence of quality.

The saving and the cost of proving it are reported as separate columns — rolled
together, a fully-sampled run reports a 1× saving, the router looking worthless
because the audit was billed to it.

## Integrity rules the instrument enforces

- **Runs from different versions of a golden set are never compared.** Every run
  carries a fingerprint of the exact cases it was scored on. Without it the
  table picked a free model measured on 90 easy cases over one measured on the
  592-case redesign — and `state/text_runs` turned out to hold **four** distinct
  exams being read as one. A model on an older exam is excluded and named as
  needing re-measurement, never silently ranked.
- **Both sides of the exam have to be hard.** The faithful cases were verbatim
  copies of the source, so the question was *is this passage supported by
  itself* — false-alarm rates sat at 0-3% across seven models and that axis
  measured nothing. Faithful cases are now mechanical rewrites that provably
  preserve support (reordered, shortened, split at a comma, aside removed), and
  the axis came alive: 0-11%, changing the ranking.
- **Every failure mode gets an equal share, and a class that cannot fill its
  share is named.** The classes that separated models were the rarest —
  `scope-widen` supplied 39 cases and was missed 41% of the time while
  `unsupported-addition` supplied 169 and was missed 14%, so the exam spent
  itself on questions nobody got wrong.
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

## Pointing does not transfer at all

The same two-product experiment, run on the pointing sets:

| | judging | pointing |
|---|---|---|
| rank correlation between products | **0.83** | **0.49** |
| how models moved | all in the same direction, median −22 | **different directions**, −4 to +43 |
| ranking | broadly held | scrambled — 1st→4th, 4th→1st |

Judging quality is portable in *order* if not in level. **Pointing quality is not
portable at all.** A model that clicks well on one product tells you close to
nothing about another, which makes a published leaderboard nearly useless for
any agent that has to drive rather than watch.

*Caveat stated because it is load-bearing: on one of the two products the top
four models are within 4 points of each other on 30 targets, so that half of the
comparison separates almost nobody. The conclusion rests on the direction and
spread of the changes, not on the exact correlation.*

## What can point at it today, and what cannot

**It speaks one protocol: OpenAI's `/v1/chat/completions`, streaming included.**
Anything that takes an OpenAI-compatible base URL works with one environment
variable and no code change — which covers Midscene (this vault's actual
consumer), the OpenAI SDKs, LangChain, LlamaIndex, and most agent frameworks.

**Claude Code does not, and an earlier draft of this file said it did.** It
speaks Anthropic's `/v1/messages`, a different shape — pointing
`ANTHROPIC_BASE_URL` here returns a 404. Translating between the two formats is
a known, bounded piece of work and it is not built, so the claim is withdrawn
rather than qualified.

Streaming was also broken until it was tested: a `stream: true` request returned
502 while the same request direct to the provider worked, and agents stream by
default. It now passes through unbuffered, with the cost read from the final
usage chunk. **Once bytes are on the wire the fallback chain is over** — a retry
after partial output would replay content the caller has already seen, so
fallback covers failing to connect, never failing part-way through.

## When the cheap model fails

A router with no fallback is **worse than no router** — it turns a provider's bad
ten minutes into the caller's outage. The chain is every model that also passed
the policy test, cheapest first, with the reference last.

Two rules make it safe, and both were learned by exercising it rather than
reasoning about it:

- **A status code alone cannot decide a router's retry.** "Never retry a 4xx" is
  right for a plain proxy and wrong here: a decommissioned model, or one no
  provider is currently serving, returns **400 or 404** — the same codes as a
  malformed request. That is the single biggest reason a chain exists and it was
  the one case the chain refused to act on. Those two codes now retry only when
  the message is about the *model*, and the full attempt trail is returned to the
  caller so a misjudgement is visible.
- **An unproven model is not a safety net.** The chain was admitting models
  measured on a superseded exam, and the first live test fell back to one that
  returns an empty answer most of the time — a 200 with nothing in it. The chain
  now applies the same exam isolation the offline table does.

**A fallback is a cost event, not just an availability one.** The table's saving
assumes the first choice answers; `superrouter.shadow` reports how often it did
not and what that actually cost.

## Staying current

A measurement describes the day it was taken, and four things move underneath it.
`python3 -m superrouter.stale` reports the three that are computable:

| what moves | how it is caught |
|---|---|
| **the exam** — a set is rebuilt, old scores stop being comparable | every run stamps the exam it was drawn from *and* the subset it sat |
| **the price** — OpenRouter's prices move weekly | the live pool is compared against the indexed one |
| **the pool** — new models appear and cannot win a race they were never entered in | same comparison, reported as a smaller question rather than a wrong answer |
| **the model itself** — providers update in place under the same name | **not computable.** This is what shadow mode is for, and shadow mode only sees drift from the reference |

Checked 2026-08-21, three days after the pool was indexed: **12 models had
appeared, one had gone, and 8 models we had measured had moved price** —
`deepseek-v4-pro` from $0.66 to $1.60 per million, `qwen3.6-27b` doubled.

**It reports and stops.** Re-measuring costs money, so it prices a refresh and
leaves the decision alone.

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

## Serve it

```bash
python3 -m superrouter.serve --port 8787
export OPENAI_BASE_URL=http://localhost:8787/v1
```

The agent declares what it is doing in the model name:

| model asked for | what happens |
|---|---|
| `superrouter/qa-vision-assert` | routed to the model measured to hold quality on judging |
| `superrouter/qa-vision-point` | routed to the model measured to hold quality on clicking |
| `superrouter/text-faithful` | routed to the model measured to hold quality on faithfulness |
| `superrouter/auto` | inferred from the request, and the choice is returned in `X-SuperRouter-Task` |
| anything else | passed through untouched |

Every routed call is logged with what it cost and what the reference would have
cost, so the saving stops being a claim from a benchmark and becomes a number
from production.

### Shadow mode — how it stays honest after the day it was measured

```bash
python3 -m superrouter.serve --shadow 20   # one call in 20 also goes to the reference
python3 -m superrouter.shadow              # read it back
```

A golden set measures the day it ran. Models are updated under the same name,
prices move weekly, and real traffic drifts away from whatever the set captured —
measured here, the same model was **22 points worse** on a product it had not
been measured on. Shadow mode is the only thing that notices while it is
happening.

The caller is unaffected: it still gets the cheap model's answer at the cheap
model's latency, and the probe runs off the response path. `shadow` then reports
agreement with an interval, the real saving including what shadowing itself
cost, and **says plainly when the sample is too small to be a verdict** rather
than printing a confident percentage over five calls.

**It runs on loopback and ships no remote mode.** It sits between the agent and
the provider, which is the most sensitive position in the stack — every prompt
and the key pass through it. On the user's own machine with the user's own key,
nothing leaves that was not already leaving. Hosted, it would be a credential
and prompt funnel for everyone using it.
