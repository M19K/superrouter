# Deploying SuperRouter somewhere that is not the machine it was built on

**Everything below is written from the user's side, not the author's.** It was
produced by running the project from a clean clone with none of the author's
data present, and by grepping the source for every assumption it makes about the
machine underneath it. Where something is missing, it says so rather than
describing the intention.

---

## Who this is for, in three stories

### Story 1 · "I use OpenRouter and I want my agent to cost less"

**This works today.** It is the only path that has been run end to end by
somebody other than the author.

```bash
git clone https://github.com/M19K/superrouter && cd superrouter
export OPENROUTER_API_KEY=sk-or-...
npm i -g agent-browser                       # required for vision sets — see below
python3 -m superrouter.pool --vision
python3 golden/qa-vision/build_generic.py --origin https://your.site --name yours
python3 -m superrouter.evals --set yours --dry-run
python3 -m superrouter.evals --set yours --model <a> --model <b>
python3 -m superrouter.route_table
python3 -m superrouter.serve --shadow 20
```

**What will surprise you:** the exam builder refuses to write a set for a page
with too little on it, rather than emitting one that scores every model at 100%.
That refusal is deliberate and it is not a failure to route around.

### Story 2 · "I use Azure OpenAI / Bedrock / Anthropic direct / a self-hosted vLLM"

**This does not work today, and it is the single largest gap in the project.**

Three files reach OpenRouter by name:

| file | what it assumes |
|---|---|
| `superrouter/evals.py` | scoring calls go to `openrouter.ai/api/v1/chat/completions` |
| `superrouter/pool.py` | the model index and its prices come from OpenRouter's catalogue |
| `superrouter/stale.py` | price drift is detected by re-reading that same catalogue |

Only one escape hatch exists: a model id prefixed `local/` is sent to
`LOCAL_MODEL_BASE_URL` (default `http://localhost:11434/v1`, i.e. Ollama). That
covers a local model and nothing else — not Azure, not Bedrock, not a hosted
vLLM, not Anthropic's own API.

**What it would take**, stated so it can be judged rather than guessed at: a
per-model record carrying `provider`, `base_url`, `api_key`, `input_price`,
`output_price`, `max_tokens` and `context_limit`, read from a YAML file, with
`pool.py` able to take prices from that file instead of a catalogue.
`ulab-uiuc/LLMRouter`'s `serve/config.py` already models exactly this and is
Apache-compatible MIT — **adopt its shape rather than inventing another.**

Until then: if you are not on OpenRouter, this project can measure nothing for
you.

### Story 3 · "I want to point Claude Code at it"

**Works, with one thing missing.** `ANTHROPIC_BASE_URL` at the proxy is enough;
the Anthropic dialect is translated at the edge.

**But `cache_control` and `thinking` are accepted and silently dropped** — a
caller asking for prompt caching gets a correct answer at an uncached price. The
response header says so; nothing in the body does. If your agent depends on
caching for its economics, this will quietly cost you money rather than save it.

---

## Hidden dependencies, found by grep rather than recall

| What | Needed for | If it is missing |
|---|---|---|
| **`agent-browser` (npm, global)** | building *any* vision golden set | the builder fails; text tasks still work |
| **A browser it can drive** | same | same |
| **Ollama** | `local/…` models only | those models are unreachable; hosted ones fine |
| **Python 3.14** | everything | untested below it; no floor has been established |
| **An OpenRouter account** | scoring, the pool index, staleness | see Story 2 |
| **`~/Documents/Mikoshi`** | *nothing any more* | was hardcoded in two places; both fixed 2026-08-23 |

**Both were shipped broken and found only by running from a clean clone**, not
by review: `stability.py` resolved its run directory against the author's home
directory, and `golden/text-faithful/build.py` defaulted `--vault` to it. Both
are fixed — the second by making the argument required, because a default that
exists on one machine answers the question wrongly everywhere else.

---

## What a user must decide that the tool will not decide for them

- **The quality bar.** It reports what each model catches and what it invents. How many missed faults your product can live with is a judgement about your product.
- **Which model is the reference.** Everything is measured against it, so a badly chosen reference makes every number meaningless.
- **How often to re-measure.** `superrouter.stale` reports drift; it never spends money on its own.
- **Whether to escalate.** `--cascade` is off by default because escalation costs are real and belong to the policy.

---

## Known gaps, in the order they will bite

1. **Not provider-agnostic.** Story 2. Everything else is downstream of this.
2. ~~`--vault` defaulted to the author's path~~ — **fixed 2026-08-23**: it is now a required argument, because a default that exists on one machine answers the question wrongly everywhere else.
3. **No `/health` endpoint.** A deployment that cannot be probed cannot be supervised. LLMRouter's serve layer has one; ours does not.
4. **`max_tokens` is passed through unclamped.** LLMRouter clamps to each model's own limit; we do not, so a request larger than a small model accepts fails at the provider rather than being caught.
5. **No context-length check.** A prompt longer than the routed model's window is discovered by the provider rejecting it.
6. **No key rotation.** One key per provider, taken from the environment.
7. **Windows is untested.** Paths are joined properly, but nothing has run there.
8. **Python floor unestablished.** Developed on 3.14; the lowest working version is unknown.

---

## What has actually been verified, and by whom

| Claim | Checked how |
|---|---|
| runs from a clean clone with no data | run cold 2026-08-23; four defects found and fixed |
| the cost-aware label generalises | LLMRouter's own xRouteBench — 3.6× cheaper at identical quality across ten splits |
| the verifier beats chance | held against random at its own escalation rate, asserted in the audit |
| every published number matches its source | `superrouter.audit --strict` re-derives them |
| it helps on real work | the Mikoshi synthesis layer, graded by that project's eval, not ours |

**Nothing here has been run by a third party on hardware the author does not
own.** That is the next thing that should happen, and no amount of internal
checking substitutes for it.
