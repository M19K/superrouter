#!/usr/bin/env python3
"""
evals.py — is a cheaper model still doing QA correctly? Answer with numbers.

Built to the same shape as 05-Orchestrator/funnel/evals.py, which measures the
vault's retrieval at 85% right-answer-first: a fixed golden set, a scored run,
a number that moves. Nothing here is a new instrument; it is that instrument
pointed at a different question.

**The question.** A QA step is one screenshot and one statement, answered true
or false. That is literally Midscene's `aiAssert`, which the QA protocol's
behaviour and appearance layers are made of, so scoring it scores the real
workload rather than a proxy for it.

**Three numbers, because one would lie.**

  Verdict accuracy   all 40 cases. The headline.
  Catch rate         the 6 cases that can only be answered correctly by seeing
                     the injected defect. This is what QA is FOR.
  False-alarm rate   how often the model calls a healthy screen broken. A model
                     with a perfect catch rate and a 30% false-alarm rate
                     produces a bug report nobody trusts, and an untrusted
                     report is the same as no report.

**Why all three.** The golden set is balanced 20 true / 20 false, so a model
that answers "true" to everything scores exactly 50% accuracy — and 0% catch.
A model that answers "false" to everything scores 50% accuracy and 100% catch.
Either one is useless. Only the three numbers together say whether a model can
do this job.

    python3 -m superrouter.evals --dry-run                     # costs nothing
    python3 -m superrouter.evals --model google/gemini-2.5-flash-lite
    python3 -m superrouter.evals --model a/b --model c/d       # compare
"""
import argparse
import base64
import json
import os
import sys
import time
import concurrent.futures as futures
import math
import ssl
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# The key label this project is entitled to — its folder name, per
# CLAUDE.md. QA against a product bills that product, never this one.
PROJECT = "superrouter"

GOLDEN_FP = {}   # set once per run, stamped into every summary
WANT_REASONING = {}

CODE = os.path.dirname(HERE)
# Task types the same scorer handles. The axes — catch rate and false-alarm
# rate — are identical across them, which is the claim: this is a way of
# defining quality, not vision tooling. A task supplies its cases; whether the
# input carries an image is a detail of the request, not of the measurement.
TASKS = {
    "qa-vision-assert": {"golden": os.path.join(CODE, "golden", "qa-vision"),
                         "runs": os.path.join(CODE, "state", "runs"),
                         "image": True},
    "text-faithful": {"golden": os.path.join(CODE, "golden", "text-faithful"),
                      "runs": os.path.join(CODE, "state", "text_runs"),
                      "image": False},
}
GOLDEN = TASKS["qa-vision-assert"]["golden"]
FRAMES = os.path.join(GOLDEN, "frames")
RUNS = TASKS["qa-vision-assert"]["runs"]
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# A model that is not on OpenRouter is still a model the vault routes to. The
# QA protocol drives Midscene against Ollama on this machine, so the pool has to
# reach a local endpoint or it cannot measure the engine the vault actually runs.
# Prefix a model with `local/` to send it there; it costs nothing per call and
# needs no credential.
LOCAL_BASE = os.environ.get("LOCAL_MODEL_BASE_URL", "http://localhost:11434/v1")


# A model id is not one endpoint. Measured 2026-08-20: three of the nine models
# in our ladders are served at more than one numeric precision — bf16, fp8 and
# unspecified — by different providers at different prices and uptimes, and
# OpenRouter picks one per request. So a score attached to a bare model name is
# an average over whichever providers happened to answer, and it cannot be
# reproduced. Pin the provider and the score means something.
_FORCED = {}


def reasoning_is_forced(model):
    """Does this model reason whether you want it to or not?

    OpenRouter says so per model, and `pool.json` already carries it as
    `reasoning_forced`. Where it is False the caller controls the burn — and
    until 2026-08-22 this harness never did, so it paid for and waited on
    reasoning it then discarded: **927,328 reasoning tokens across every scored
    run**, 599 per call on `qwen/qwen3.7-flash` against a prompt whose entire
    answer is the word TRUE or FALSE.

    That is this project's own recurring failure one layer out — the instrument
    charged the model for a setting the instrument chose, and published the
    result as the model's cost and the model's latency.

    An unknown model is treated as forced. Sending a switch a model does not
    understand is exactly the failure that comes back as an empty answer.
    """
    if not _FORCED:
        try:
            pool = json.load(open(os.path.join(CODE, "state", "pool.json")))
            _FORCED.update({m["id"]: bool(m.get("reasoning_forced"))
                            for m in pool["models"]})
        except Exception:
            _FORCED["_unreadable"] = True
    return _FORCED.get(model.replace("local/", ""), True)


def endpoint_for(model):
    """Returns (url, api_key_override, wire_name). Local models bill nothing and
    Ollama ignores the key's value but requires the header to exist."""
    if model.startswith("local/"):
        return (LOCAL_BASE.rstrip("/") + "/chat/completions",
                "ollama-local-no-key-needed", model[len("local/"):])
    return ENDPOINT, None, model

# The statement is presented the way Midscene presents one: an assertion about
# what is on screen, answered with a single word. No chain of thought is asked
# for, because the real workload does not ask for one and paying for reasoning
# the caller discards is exactly the waste this project exists to find.
PROMPT = (
    "You are checking a screenshot of a web page during a QA pass.\n\n"
    "Statement: {assertion}\n\n"
    "Is that statement true of the screenshot? Answer with exactly one word: "
    "TRUE or FALSE."
)


def key():
    """The key comes from the vault's resolver, which hands back the label
    belonging to THIS project and nothing else.

    Measured across the vault on 2026-08-20: four products shared one key and
    94% of $25.27 of spend could not be attributed to any of them. Providers
    attribute by key, and OpenRouter's history reaches back only 30 days, so
    attribution missed is attribution gone. Borrowing another product's key puts
    this project's bill on that project's account permanently.

    The resolver never falls back to another label — a missing key raises and
    says what to do, because a resolver that quietly substitutes whatever key
    exists is exactly how the misattribution happened.

    Outside the vault, `OPENROUTER_API_KEY` or a gitignored `secrets.json`.
    Never printed, never logged, never written into a run record.
    """
    vault = os.path.expanduser("~/Documents/Mikoshi/05-Orchestrator")
    if os.path.isdir(os.path.join(vault, "ledger")):
        sys.path.insert(0, vault)
        try:
            from ledger.keys import resolve
            return resolve("openrouter", project=PROJECT)
        except ImportError:
            pass
        finally:
            sys.path.remove(vault)
    k = os.environ.get("OPENROUTER_API_KEY")
    if k:
        return k
    local = os.path.join(CODE, "secrets.json")
    if os.path.exists(local):
        return json.load(open(local))["openrouter_key"]
    raise SystemExit(
        "No OpenRouter key. Inside the vault this resolves from "
        "05-Orchestrator/ledger/keys.md; outside it, set OPENROUTER_API_KEY or "
        "put one in code/secrets.json (gitignored)."
    )


def fingerprint(cases):
    """A short, stable id for the exact exam a run was scored on.

    Golden sets change — classes get rebalanced, harder cases get added, a
    product is rebuilt. A model measured on the old set and one measured on the
    new set have not sat the same exam, and comparing their scores ranks the
    exams rather than the models. Measured 2026-08-21: the routing table picked
    a free model scored on a 90-case set over one scored on the 592-case
    redesign, because nothing in the record said they were different exams.

    So every run carries the fingerprint of what it sat, and anything that
    compares runs refuses to mix them.
    """
    import hashlib
    h = hashlib.sha256()
    for c in sorted(cases, key=lambda c: c["id"]):
        h.update(f"{c['id']}|{c.get('answer')}|{c.get('corruption') or ''}"
                 f"|{c.get('variant') or ''}|{c.get('defect') or ''}".encode())
    return h.hexdigest()[:12]


def golden(task="qa-vision-assert"):
    """Generated manifests keep their cases under `case_list`."""
    m = json.load(open(os.path.join(TASKS[task]["golden"], "manifest.json")))
    m["cases"] = m.get("case_list") or m.get("cases")
    return m


def frame_b64(name, task="qa-vision-assert"):
    if not TASKS[task]["image"]:
        return None
    with open(os.path.join(TASKS[task]["golden"], "frames", f"{name}.png"), "rb") as f:
        return base64.b64encode(f.read()).decode()


# A run is hundreds of calls over ten-odd minutes, so a transient network fault
# is not an edge case, it is a certainty. One killed the first full ladder after
# the reference model had already been scored and paid for. Retry the transport,
# never the verdict.
# json.JSONDecodeError belongs here: under concurrency OpenRouter interleaves
# `: OPENROUTER PROCESSING` keep-alive comment lines into the body, which is
# valid for its transport and not valid JSON. That is a transport fault wearing
# a parser's clothes, and it must be retried, not counted as a wrong answer.
TRANSPORT_FAULTS = (urllib.error.HTTPError, urllib.error.URLError,
                    ssl.SSLError, TimeoutError, ConnectionError, OSError,
                    json.JSONDecodeError)


def ask(model, assertion, image_b64, api_key, timeout=120, tries=3, provider=None,
        want_reasoning=False):
    """One call. `usage.include` makes OpenRouter return what it actually
    charged, so cost is a reading rather than an estimate."""
    url, key_override, wire = endpoint_for(model)
    payload = {
        "model": wire,
        "max_tokens": 2000,
        "temperature": 0,
        "usage": {"include": True},
        "messages": [{"role": "user", "content": (
            [{"type": "text", "text": assertion}] +
            ([{"type": "image_url",
               "image_url": {"url": f"data:image/png;base64,{image_b64}"}}]
             if image_b64 else [])
        )}],
    }
    # Ask for no reasoning wherever the model allows it to be switched off.
    # Ollama's OpenAI-compatible endpoint has no such field, which is where the
    # belief that it cannot be done came from — true of Ollama, false here.
    asked_no_reasoning = (not model.startswith("local/")
                          and not reasoning_is_forced(model)
                          and not want_reasoning)
    if asked_no_reasoning:
        payload["reasoning"] = {"enabled": False}
    if provider and not model.startswith("local/"):
        # `allow_fallbacks: False` is the half that matters — without it
        # OpenRouter silently serves from somewhere else when the pinned
        # provider is busy, and the run is unpinned again without saying so.
        payload["provider"] = {"order": [provider], "allow_fallbacks": False}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key_override or api_key}",
        "Content-Type": "application/json",
        "X-Title": "superrouter-evals",
    })
    t0 = time.time()
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
            # strip keep-alive comment lines before parsing
            body = "\n".join(l for l in raw.splitlines() if not l.startswith(":")).strip()
            d = json.loads(body)
            break
        except urllib.error.HTTPError as e:
            # 4xx is the request being wrong and will stay wrong. 5xx and 429
            # are the far end having a moment.
            if e.code < 500 and e.code != 429:
                raise
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)
        except TRANSPORT_FAULTS:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)
    took = time.time() - t0
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    usage = d.get("usage") or {}
    return {
        "local": model.startswith("local/"),
        # What was asked for and what came back are different facts. Measured
        # 2026-08-22: `liquid/lfm-2.5-2.6b:free` is listed as optional and
        # returned 971 reasoning tokens per call with the switch set to off —
        # the flag is a REQUEST, and only the response says whether it was
        # honoured. Trusting the index here is the same failure as trusting a
        # published price instead of reading the bill.
        "reasoning_asked_off": asked_no_reasoning,
        # who actually served it — recorded on every call, so a score can always
        # be traced to the endpoint that produced it
        "served_by": d.get("provider"),
        "text": (msg.get("content") or "").strip(),
        "cost": float(usage.get("cost") or 0),
        "in_tokens": usage.get("prompt_tokens"),
        "out_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": ((usage.get("completion_tokens_details") or {})
                             .get("reasoning_tokens")),
        "seconds": round(took, 2),
    }


def read_verdict(text):
    """Return True, False, or None when the model did not answer the question.
    An unparseable answer is counted as wrong, never quietly dropped — a model
    that will not answer in the required shape cannot drive a QA run."""
    t = text.strip().upper()
    if not t:
        return None
    head = t.replace("*", "").replace("`", "").lstrip("# ").strip()
    if head.startswith("TRUE"):
        return True
    if head.startswith("FALSE"):
        return False
    if "TRUE" in t and "FALSE" not in t:
        return True
    if "FALSE" in t and "TRUE" not in t:
        return False
    return None


def effective_workers(model, asked):
    """One at a time for a local model, whatever was asked for.

    Ollama serves a single request at a time, so N in flight does not run N
    times faster — it makes each one wait N times longer and then time out. The
    cap costs nothing in wall time because the server was serialising anyway,
    and it removes the only way this harness can manufacture a failure and
    attribute it to the model.
    """
    return 1 if model.startswith("local/") else asked


def score(model, cases, api_key, verbose=False, workers=8, task="qa-vision-assert"):
    workers = effective_workers(model, workers)
    """Score every case. Concurrent, because a 140-case run one-at-a-time is
    four minutes of waiting per model and iteration speed is the whole point.
    Order is restored afterwards so run records stay diffable."""
    cache = {}
    for c in cases:
        cache.setdefault(c.get("frame", c["id"]),
                         frame_b64(c["frame"], task) if c.get("frame") else None)

    def one(idx_case):
        i, c = idx_case
        try:
            body = (PROMPT.format(assertion=c["assert"])
                    if TASKS[task]["image"] else c["assert"])
            r = ask(model, body, cache[c.get("frame", c["id"])], api_key,
                    want_reasoning=WANT_REASONING.get("v", False))
        except TRANSPORT_FAULTS as e:
            detail = ""
            if isinstance(e, urllib.error.HTTPError):
                try:
                    detail = e.read().decode()[:160]
                except Exception:
                    pass
            return i, {**c, "said": None, "correct": False, "cost": 0.0, "seconds": 0.0,
                       "error": f"{e} {detail}".strip()}
        said = read_verdict(r["text"])
        return i, {**c, "said": said, "raw": r["text"][:80], "correct": said is c["answer"],
                   "cost": r["cost"], "seconds": r["seconds"],
                   "in_tokens": r["in_tokens"], "out_tokens": r["out_tokens"],
                   "reasoning_tokens": r["reasoning_tokens"],
                   "reasoning_asked_off": r.get("reasoning_asked_off")}

    out = [None] * len(cases)
    with futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, res in ex.map(one, enumerate(cases)):
            out[i] = res
    errors = sum(1 for r in out if r.get("error"))
    if verbose:
        for r in out:
            if not r["correct"]:
                print(f"  wrong {r['id']} [{r['frame']}] said {r['said']}, answer {r['answer']}"
                      f"  — {r['assert'][:60]}")
    return out, errors


def wilson(k, n, z=1.96):
    """A percentage with no interval beside it reads as a ranking it has not
    earned. Six cases put an observed 83% anywhere between 44% and 97%."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h) * 100), round(min(1.0, c + h) * 100)


def summarise(model, results, errors):
    n = len(results)
    correct = sum(r["correct"] for r in results)
    defect = [r for r in results if r["needs_defect_sight"]]
    caught = sum(r["correct"] for r in defect)
    # A false alarm is a healthy screen called broken: the statement is true of
    # a healthy frame and the model said it was not.
    healthy_ok = [r for r in results
                  if r["answer"] is True and not r["needs_defect_sight"]
                  and not str(r.get("frame", "")).startswith("broken-")]
    alarms = sum(1 for r in healthy_ok if r["said"] is not True)
    # **A model that was never asked has not refused.** Reported by
    # @claude-code/product-portfolio 2026-08-22: 41 client timeouts against a
    # local model were counted as refusals, the summary read `refused 27%`, and
    # the rule "a model refusing half its cases is unusable" would have
    # disqualified a model that never saw the request. Errors and refusals were
    # literally the same 41 rows sharing a column.
    #
    # They are different failures and only one of them is the model's:
    #   error   — the request did not complete. Ours, or the network's.
    #   refusal — the model answered, and the answer carried no verdict.
    reached = [r for r in results if not r.get("error")]
    unparsed = sum(1 for r in reached if r["said"] is None)
    # A model that will not answer in the required shape has not answered
    # wrongly — it has not answered. Scoring it as wrong hides a distinct and
    # disqualifying failure: it cannot drive a QA run at all. Report both the
    # score including refusals (what you get in practice) and the score over
    # answers actually given (what the model can do when it does reply).
    answered = [r for r in results if r["said"] is not None]
    ans_defect = [r for r in answered if r["needs_defect_sight"]]
    # A refusal on a healthy screen is NOT a false alarm. Measured 2026-08-21:
    # z-ai/glm-5.2:free returned an empty string to every case and the headline
    # read "false alarms 98%" — describing a model screaming about defects when
    # it had in fact said nothing at all. Same shape as the shadow-mode bug: an
    # instrument must never blame what it measures for its own failure to read
    # an answer. So the failure-mode rates are computed over answers actually
    # given, and refusals are reported as their own column, always.
    ans_healthy = [r for r in healthy_ok if r["said"] is not None]
    ans_alarms = sum(1 for r in ans_healthy if r["said"] is not True)
    cost = sum(r["cost"] for r in results)
    secs = sum(r["seconds"] for r in results)
    by_defect = {}
    for r in results:
        if r["needs_defect_sight"]:
            by_defect.setdefault(r.get("defect") or r.get("corruption"),
                                 []).append(r["correct"])
    missed = sorted(d for d, v in by_defect.items() if not any(v))

    return {
        "model": model, "cases": n,
        "reasoning_off": bool(results and results[0].get("reasoning_asked_off")),
        "reasoning_per_call": (round(sum(r.get("reasoning_tokens") or 0
                                         for r in results) / n) if n else 0),
        "golden_fingerprint": GOLDEN_FP.get("v"),      # the subset actually sat
        "exam_fingerprint": GOLDEN_FP.get("exam"),     # the set it was drawn from
        "exam_cases": GOLDEN_FP.get("exam_n"),
        "usable": errors == 0 and unparsed == 0,
        "catch_ci": wilson(caught, len(defect)),
        "false_alarm_ci": wilson(alarms, len(healthy_ok)),
        "defect_classes": len(by_defect),
        "refusals": unparsed,
        # denominator is what actually reached the model, not what we sent
        "refusal_pct": round(100 * unparsed / len(reached)) if reached else 0,
        "reached": len(reached),
        "error_pct": round(100 * (n - len(reached)) / n) if n else 0,
        "false_alarm_when_answered": (round(100 * ans_alarms / len(ans_healthy))
                                      if ans_healthy else 0),
        "answered_healthy": len(ans_healthy),
        "answered_defect": len(ans_defect),
        "catch_when_answered": (round(100 * sum(r["correct"] for r in ans_defect)
                                      / len(ans_defect)) if ans_defect else 0),
        "accuracy_when_answered": (round(100 * sum(r["correct"] for r in answered)
                                         / len(answered)) if answered else 0),
        "defect_classes_missed_entirely": missed,
        "accuracy": round(100 * correct / n) if n else 0,
        "catch": round(100 * caught / len(defect)) if defect else 0,
        "catch_n": f"{caught}/{len(defect)}",
        "false_alarm": round(100 * alarms / len(healthy_ok)) if healthy_ok else 0,
        "false_alarm_n": f"{alarms}/{len(healthy_ok)}",
        "unparseable": unparsed, "errors": errors,
        "cost_usd": round(cost, 6),
        "cost_per_case_usd": round(cost / n, 8) if n else 0,
        "seconds": round(secs, 1),
        "seconds_per_case": round(secs / n, 2) if n else 0,
        "reasoning_tokens": sum(r.get("reasoning_tokens") or 0 for r in results),
    }


def dry_run(cases):
    """What a scored run would cost, before spending anything. An estimate and
    labelled as one — the token count for an image is the model's business, not
    the caller's, so this brackets it rather than pretending to know it."""
    # A text task has no frames. The dry run is the one place that must not
    # crash — it exists so nobody spends money without seeing the bill first.
    frames = sorted({c["frame"] for c in cases if "frame" in c})
    px = {}
    from struct import unpack
    for f in frames:
        d = open(os.path.join(FRAMES, f"{f}.png"), "rb").read(33)
        px[f] = unpack(">II", d[16:24])
    calls = len(cases)
    if not frames:
        # A text task: the prompt IS the cost, and its length is known exactly,
        # so this is a count rather than a bracket. Four characters per token is
        # the usual rule of thumb and is labelled as one.
        chars = sum(len(c.get("assert") or c.get("text") or "") for c in cases)
        lo = hi = chars // 4
    else:
        # Bracket: 28px patches (Qwen-family, dense) as the high end; OpenAI's
        # 512px-tile accounting as the low end. Both are documented conventions.
        hi = sum(((px[c["frame"]][0] // 28) * (px[c["frame"]][1] // 28)) for c in cases)
        lo = sum((-(-px[c["frame"]][0] // 512) * -(-px[c["frame"]][1] // 512) * 170 + 85)
                 for c in cases)
    where = f"over {len(frames)} frames" if frames else "of text, no images"
    print(f"dry run · {calls} calls {where}, no money spent\n")
    if frames:
        print(f"  image tokens across the run, bracketed: {lo:,} … {hi:,}")
    else:
        print(f"  input tokens across the run: ~{lo:,} (counted from the prompts)")
    print(f"  output tokens: ~{calls * 3:,} at one word each, if the model does not reason\n")
    print(f"  {'$/M in':>8}  {'est. run cost':>13}  model")
    pool = json.load(open(os.path.join(CODE, "state", "pool.json")))["models"]
    for m in sorted((m for m in pool if (m["vision"] or not frames)
                     and m["in_per_m"] > 0),
                    key=lambda m: m["in_per_m"])[:10]:
        c_lo = lo / 1e6 * m["in_per_m"] + calls * 3 / 1e6 * m["out_per_m"]
        c_hi = hi / 1e6 * m["in_per_m"] + calls * 3 / 1e6 * m["out_per_m"]
        flag = " ⟲ reasoning forced, output cost not bounded by the above" if m["reasoning_forced"] else ""
        print(f"  {m['in_per_m']:8.3f}  ${c_lo:.5f}–${c_hi:.5f}  {m['id']}{flag}")
    print("\n  These are estimates from published prices. The real number comes back")
    print("  from OpenRouter with each call and is what a scored run reports.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", default=[], help="repeatable")
    ap.add_argument("--dry-run", action="store_true", help="cost the run, spend nothing")
    ap.add_argument("--limit", type=int, help="score only the first N cases")
    ap.add_argument("--verbose", action="store_true", help="print every wrong answer")
    ap.add_argument("--provider", default=None, metavar="NAME",
                    help="pin the serving provider (e.g. DeepInfra). Three of our "
                         "measured models are served at more than one numeric "
                         "precision, so an unpinned score is an average over "
                         "whichever endpoint answered and cannot be reproduced.")
    ap.add_argument("--workers", type=int, default=8, help="concurrent requests per model")
    ap.add_argument("--reasoning", action="store_true",
                    help="leave reasoning ON where the model allows it to be off. "
                         "The same model at two settings is two candidates with "
                         "different cost, latency and quality — measure both.")
    ap.add_argument("--task", choices=list(TASKS), default="qa-vision-assert")
    ap.add_argument("--set", dest="set_dir",
                    help="a generated set directory (golden/qa-vision/sets/<name>)")
    ap.add_argument("--sample", type=int,
                    help="stratified sample of N cases — balanced true/false and "
                         "spread across defect classes, so a subset is a smaller "
                         "measurement rather than a narrower one")
    a = ap.parse_args()

    if a.set_dir:
        base = a.set_dir if os.path.isabs(a.set_dir) else os.path.join(
            CODE, "golden", "qa-vision", "sets", a.set_dir)
        TASKS[a.task] = {**TASKS[a.task], "golden": base,
                         "runs": os.path.join(CODE, "state",
                                              f"runs_{os.path.basename(base)}")}
    g = golden(a.task)
    cases = g["cases"]
    all_cases = list(cases)          # before any sampling — this is the exam
    if a.sample:
        # Stratify by defect class and by answer, then take round-robin. A random
        # subset would under-represent the rare classes, which are exactly the
        # ones that separate models.
        import collections
        buckets = collections.defaultdict(list)
        for c in cases:
            buckets[(c.get("defect"), c["answer"])].append(c)
        picked, keys = [], sorted(buckets, key=lambda k: (str(k[0]), k[1]))
        i = 0
        while len(picked) < a.sample and any(buckets[k] for k in keys):
            k = keys[i % len(keys)]
            if buckets[k]:
                picked.append(buckets[k].pop())
            i += 1
        t = sum(1 for c in picked if c["answer"])
        # trim the majority side so the constant-answer baseline stays 50%
        while t * 2 > len(picked):
            for j, c in enumerate(picked):
                if c["answer"]:
                    picked.pop(j); t -= 1; break
        while (len(picked) - t) * 2 > len(picked):
            for j, c in enumerate(picked):
                if not c["answer"]:
                    picked.pop(j); break
        cases = picked
    elif a.limit:
        cases = cases[: a.limit]
    true_n = sum(1 for c in cases if c["answer"])
    print(f"golden set · {len(cases)} cases · {true_n} true / {len(cases) - true_n} false "
          f"· constant-answer baseline {round(100 * max(true_n, len(cases) - true_n) / len(cases))}%\n")

    runs_dir = TASKS[a.task]["runs"]
    if a.dry_run or not a.model:
        dry_run(cases)
        return

    # Two different identities, and conflating them was wrong.
    #
    #   the EXAM    — the whole golden set as built. Changes when the set is
    #                 rebuilt, and that is what "your measurement is out of
    #                 date" means.
    #   the SAMPLE  — the subset this run actually sat, when --limit is used.
    #                 Two runs are comparable only if they sat the same cases.
    #
    # Stamping only the sample made every --limit run look like a different
    # exam from its own golden set, so the staleness check reported 0 of 13
    # models measured on the current set when six had just been measured on it.
    WANT_REASONING["v"] = a.reasoning
    GOLDEN_FP["exam"] = fingerprint(all_cases)
    GOLDEN_FP["exam_n"] = len(all_cases)
    GOLDEN_FP["v"] = fingerprint(cases)
    api_key = key()
    os.makedirs(runs_dir, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
    rows = []
    for model in a.model:
        print(f"scoring {model} …")
        results, errors = score(model, cases, api_key, verbose=a.verbose,
                                workers=a.workers, task=a.task)
        s = summarise(model, results, errors)
        rows.append(s)
        with open(os.path.join(runs_dir, f"{stamp}_{model.replace('/', '_')}.json"), "w") as f:
            json.dump({"summary": s, "results": results}, f, indent=1)
        if s.get("error_pct"):
            print(f"  {s['error_pct']}% of requests never completed — timeout or "
                  f"transport, not the model. Everything below is over the "
                  f"{s['reached']} that reached it.")
        if s["refusal_pct"]:
            print(f"  REFUSED {s['refusal_pct']}% of what reached it — answered, "
                  f"but with no verdict in the answer.")
        print(f"  accuracy {s['accuracy']}%  catch {s['catch_when_answered']}% "
              f"({s['answered_defect']} answered)  false alarms "
              f"{s['false_alarm_when_answered']}% ({s['answered_healthy']} answered)  "
              f"refused {s['refusal_pct']}%  "
              f"${s['cost_usd']:.5f}  {s['seconds']}s wall\n")

    print(f"{'accuracy':>9} {'catch':>7} {'false alarm':>12} {'refused':>8} "
          f"{'errored':>8} {'$ / run':>10} {'s / case':>9}  model")
    print("  catch and false alarm are over answers actually GIVEN. `refused` is the")
    print("  model declining to answer; `errored` is the request never arriving, which")
    print("  is ours and never counts against the model.")
    for s in sorted(rows, key=lambda s: (s["refusal_pct"], -s["accuracy"])):
        flag = "  ← unusable" if s["refusal_pct"] >= 50 else ""
        print(f"{s['accuracy']:8}% {s['catch_when_answered']:6}% "
              f"{s['false_alarm_when_answered']:11}% {s['refusal_pct']:7}% "
              f"{s.get('error_pct', 0):7}% {s['cost_usd']:10.5f} "
              f"{s['seconds_per_case']:9.2f}  {s['model']}{flag}")
    print(f"\nrun records → {runs_dir}")


if __name__ == "__main__":
    main()
