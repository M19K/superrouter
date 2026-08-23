#!/usr/bin/env python3
"""
providers.py — reach any OpenAI-compatible endpoint, not only OpenRouter.

    python3 -m superrouter.providers            # what this machine can reach
    python3 -m superrouter.providers --check    # and whether each one answers

**The gap this closes.** Until 2026-08-23 three files named `openrouter.ai`
directly, and the only way out was a `local/` prefix pointing at Ollama. Anyone
on Azure OpenAI, Bedrock, Anthropic's own API, Together, Fireworks or a
self-hosted vLLM could measure nothing at all — which is most of the people who
would want this.

**Shape borrowed rather than invented.** `ulab-uiuc/LLMRouter`'s
`serve/config.py` already models a provider well: name, base URL, key, prices,
token ceiling, context window. That is the right decomposition and it is MIT, so
this follows it. What changed is the file format — **JSON, not YAML**, because
PyYAML is a dependency and this project's whole install story is that there
isn't one.

## The file

`providers.json` beside the repo root, or wherever `SUPERROUTER_PROVIDERS`
points. Absent, the defaults below reproduce today's behaviour exactly, so an
existing setup does not change.

```json
{
  "providers": {
    "azure":  {"base_url": "https://you.openai.azure.com/openai/v1",
               "api_key_env": ["AZURE_OPENAI_KEY"]},
    "vllm":   {"base_url": "http://gpu-box:8000/v1", "api_key_env": []}
  },
  "models": {
    "azure/gpt-4o":  {"provider": "azure",  "model_id": "gpt-4o",
                      "in_per_m": 2.5, "out_per_m": 10.0,
                      "max_tokens": 16384, "context": 128000},
    "vllm/qwen-72b": {"provider": "vllm",   "model_id": "Qwen/Qwen2.5-72B",
                      "in_per_m": 0.0, "out_per_m": 0.0,
                      "max_tokens": 4096, "context": 32768}
  }
}
```

**Prices are declared, not discovered, for anything that is not OpenRouter.**
Only OpenRouter publishes a catalogue we can read, and a cost figure derived
from a price nobody entered is a guess wearing a decimal point. A model with no
price is reported as unpriced rather than as free.

**Keys are lists.** One key is the common case; several rotate round-robin,
which is what a shared account under a rate limit needs. Values are read from
the environment and never from the file, so the file can be committed.
"""
import argparse
import itertools
import json
import os

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Defaults reproduce the behaviour that existed before this file, so adding it
# changes nothing for anyone already running.
BUILTIN = {
    "providers": {
        "openrouter": {"base_url": "https://openrouter.ai/api/v1",
                       "api_key_env": ["OPENROUTER_API_KEY"],
                       "catalogue": "openrouter"},
        "local": {"base_url": os.environ.get("LOCAL_MODEL_BASE_URL",
                                             "http://localhost:11434/v1"),
                  "api_key_env": []},
    },
    "models": {},
}

_ROTATION = {}


def path():
    return os.environ.get("SUPERROUTER_PROVIDERS") or os.path.join(CODE, "providers.json")


def load():
    cfg = {"providers": dict(BUILTIN["providers"]), "models": dict(BUILTIN["models"])}
    p = path()
    if os.path.exists(p):
        try:
            user = json.load(open(p))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{p} is not valid JSON: {e}")
        cfg["providers"].update(user.get("providers") or {})
        cfg["models"].update(user.get("models") or {})
    return cfg


def resolve(model, cfg=None):
    """Everything needed to call one model: url, key, wire name, limits, price.

    A model id of the form `<provider>/<rest>` is looked up in `models` first,
    then matched by its provider prefix, and finally falls through to
    OpenRouter — which is what every id did before this existed.
    """
    cfg = cfg or load()
    entry = (cfg["models"] or {}).get(model)
    if entry:
        prov_name = entry["provider"]
        wire = entry.get("model_id", model.split("/", 1)[-1])
    else:
        prefix = model.split("/", 1)[0]
        if prefix in cfg["providers"] and prefix != "openrouter":
            prov_name, wire = prefix, model.split("/", 1)[-1]
            entry = {}
        else:
            prov_name, wire, entry = "openrouter", model, {}

    prov = cfg["providers"].get(prov_name)
    if not prov:
        raise SystemExit(f"model '{model}' names provider '{prov_name}', "
                         f"which is not in {path()}")

    # Round-robin across however many keys the provider was given. One is the
    # common case; several are what a shared account under a rate limit needs.
    envs = prov.get("api_key_env") or []
    keys = [os.environ[e] for e in envs if os.environ.get(e)]
    key = None
    if keys:
        it = _ROTATION.setdefault(prov_name, itertools.cycle(range(len(keys))))
        key = keys[next(it)]

    return {
        "provider": prov_name,
        "url": prov["base_url"].rstrip("/") + "/chat/completions",
        "key": key,
        "wire": wire,
        "in_per_m": entry.get("in_per_m"),
        "out_per_m": entry.get("out_per_m"),
        "max_tokens": entry.get("max_tokens"),
        "context": entry.get("context"),
        "keys_available": len(keys),
        "declared": bool(entry),
    }


def clamp(model, asked, cfg=None):
    """Never ask a model for more output than it accepts.

    Passing a caller's `max_tokens` straight through means a request larger than
    a small model allows fails at the provider — after the request was sent and
    the latency was spent. LLMRouter clamps; this now does too. Only models with
    a declared ceiling are clamped, because an undeclared one is unknown rather
    than unlimited.
    """
    r = resolve(model, cfg)
    ceiling = r.get("max_tokens")
    if not ceiling or not asked:
        return asked, None
    if asked > ceiling:
        return ceiling, f"clamped {asked}→{ceiling} for {model}"
    return asked, None


def fits(model, prompt_chars, cfg=None):
    """Rough context check before spending. Four characters to a token is a rule
    of thumb and is labelled as one — it exists to catch the request that is
    obviously too long, not to shave the last percent."""
    r = resolve(model, cfg)
    ctx = r.get("context")
    if not ctx:
        return True, None
    est = prompt_chars // 4
    if est > ctx * 0.95:
        return False, f"~{est:,} tokens estimated against a {ctx:,} window"
    return True, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="ask each provider for its model list")
    a = ap.parse_args()
    cfg = load()
    p = path()
    print(f"providers · {p if os.path.exists(p) else 'built-in defaults only ('+p+' absent)'}\n")
    for name, prov in sorted(cfg["providers"].items()):
        envs = prov.get("api_key_env") or []
        have = [e for e in envs if os.environ.get(e)]
        status = (f"{len(have)}/{len(envs)} key(s) set" if envs else "no key needed")
        print(f"  {name:<14} {prov['base_url']:<44} {status}")
        if a.check:
            import urllib.error
            import urllib.request
            req = urllib.request.Request(prov["base_url"].rstrip("/") + "/models")
            if have:
                req.add_header("Authorization", f"Bearer {os.environ[have[0]]}")
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    n = len((json.load(r) or {}).get("data") or [])
                print(f"  {'':<14} → reachable, {n} model(s)")
            except Exception as e:
                print(f"  {'':<14} → NOT reachable: {str(e)[:60]}")
    if cfg["models"]:
        print(f"\n  {len(cfg['models'])} model(s) declared with their own prices and limits:")
        for m, e in sorted(cfg["models"].items()):
            price = (f"${e.get('in_per_m')}/${e.get('out_per_m')} per M"
                     if e.get("in_per_m") is not None else "UNPRICED")
            print(f"    {m:<30} {price:<24} ctx {e.get('context') or '?'}")
    else:
        print(f"\n  No models declared. Everything falls through to OpenRouter,")
        print(f"  which is the only provider publishing a catalogue we can read.")
        print(f"  To use Azure, Bedrock, vLLM or anything else, write {os.path.basename(p)} —")
        print(f"  the shape is in this module's docstring.")


if __name__ == "__main__":
    main()
