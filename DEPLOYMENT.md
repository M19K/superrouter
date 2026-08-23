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

**This works as of 2026-08-23.** Write `providers.json`:

```json
{
  "providers": {
    "azure": {"base_url": "https://you.openai.azure.com/openai/v1",
              "api_key_env": ["AZURE_OPENAI_KEY", "AZURE_OPENAI_KEY_2"]}
  },
  "models": {
    "azure/gpt-4o": {"provider": "azure", "model_id": "your-deployment",
                     "in_per_m": 2.5, "out_per_m": 10.0,
                     "max_tokens": 16384, "context": 128000}
  }
}
```

`python3 -m superrouter.providers --check` says what is reachable and which keys
are set. Several keys per provider rotate round-robin.

**Prices are declared, not discovered.** Only OpenRouter publishes a catalogue
we can read, so a model you add carries its own prices — and one added without
them is reported as *unpriced*, never as free.

**What was true before this:**

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
A declarative per-provider config models exactly this and is
Apache-compatible MIT — **adopt its shape rather than inventing another.**

That was the largest gap in the project and it is closed. What remains
OpenRouter-only is the *catalogue*: `pool.py` and `stale.py` discover models and
price drift from it, so on another provider you maintain that list yourself.

### Story 3 · "I want to point Claude Code at it"

**Works, with one thing missing.** `ANTHROPIC_BASE_URL` at the proxy is enough;
the Anthropic dialect is translated at the edge.

**Prompt caching is forwarded** as of 2026-08-23 — OpenRouter honours
`cache_control` on Anthropic models, and a provider that does not understand the
field ignores it. Dropping it, which is what happened before, turned a cached
prompt into an uncached bill while the response still reported success.

**`thinking` is still accepted and not acted on.** A model that reasons will
still reason; the blocks are not returned separately. The response header says
so, and it is listed here rather than left to be discovered.

---

## Hidden dependencies, found by grep rather than recall

| What | Needed for | If it is missing |
|---|---|---|
| **`agent-browser` (npm, global)** | building *any* vision golden set | the builder fails; text tasks still work |
| **A browser it can drive** | same | same |
| **Ollama** | `local/…` models only | those models are unreachable; hosted ones fine |
| **Python 3.9+** | everything | verified: every module imports and the audit runs on 3.9.6 as well as 3.14 |
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
3. ~~No `/health`~~ — **added.** `GET /health` reports whether a routing table
   is loaded and what is enabled, and calls no model to find out, because a
   health check that spends money is one nobody runs often.
4. ~~`max_tokens` unclamped~~ — **clamped** to each model's declared ceiling,
   and the adjustment is reported rather than made silently.
5. ~~No context check~~ — **added.** An oversized prompt is refused with a 413
   and an estimate, before the request is sent.
6. ~~No key rotation~~ — **added.** Several keys per provider rotate
   round-robin.
7. **Windows is unrun, with no known blocker.** Checked for the things that
   actually break — `signal`, `fcntl`, `os.fork`, hardcoded POSIX paths — and
   there are none; every path is joined rather than concatenated. That is not
   the same as it having worked, and it is listed as unrun rather than
   supported.

---

## What has actually been verified, and by whom

| Claim | Checked how |
|---|---|
| runs from a clean clone with no data | run cold 2026-08-23; four defects found and fixed |
| the cost-aware label generalises | an external public routing benchmark — 3.6× cheaper at identical quality across ten splits |
| the verifier beats chance | held against random at its own escalation rate, asserted in the audit |
| every published number matches its source | `superrouter.audit --strict` re-derives them |
| it helps on real work | the Mikoshi synthesis layer, graded by that project's eval, not ours |
| it runs on an old Python | every module imported and the audit run on 3.9.6 |
| non-OpenRouter providers resolve | Azure and vLLM configured and resolved, keys rotating, limits clamped |

**Nothing here has been run by a third party on hardware the author does not
own.** That is the next thing that should happen, and no amount of internal
checking substitutes for it.
