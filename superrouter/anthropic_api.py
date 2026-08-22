#!/usr/bin/env python3
"""
anthropic_api.py — speak Anthropic's Messages API, route underneath in OpenAI's.

Claude Code, and anything else built against `ANTHROPIC_BASE_URL`, sends
`POST /v1/messages` in a shape that is not OpenAI's. The proxy serves OpenAI's
`/v1/chat/completions`, so pointing Claude Code at it returned a 404 — the
README claimed otherwise until that was tested.

**Translation lives at the edges only.** A request is converted on the way in, a
response on the way out, and everything between — routing, the fallback chain,
shadow sampling, cost accounting — is the code that already existed and does not
know which dialect the caller spoke. That is deliberate: a second copy of the
routing logic for a second protocol is how the two quietly diverge.

## What is covered

  text · images · system prompts · stop sequences · temperature and top_p
  tools and tool results · streaming, as the full Anthropic event sequence

## What is not, stated plainly rather than discovered later

  **Prompt caching** (`cache_control`) is accepted and dropped. OpenAI's
  chat-completions dialect has no equivalent field, so a caller asking for it
  gets a correct answer at an uncached price. Silently honouring nothing while
  reporting success is the failure this note exists to prevent.

  **Extended thinking** (`thinking`) is likewise accepted and dropped; a model
  that reasons will still reason, but the blocks are not returned separately.

  **`/v1/messages/count_tokens`** is not served — it needs a tokeniser per
  model, which is a different problem from routing.
"""
import json
import time

# OpenAI says why generation ended in one vocabulary; Anthropic in another.
STOP_REASON = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}

DROPPED_SILENTLY_WOULD_BE_WORSE = ("cache_control", "thinking")


def _content_to_openai(content):
    """Anthropic content is a string or a list of typed blocks."""
    if isinstance(content, str):
        return content, [], []
    parts, tool_results, calls = [], [], []
    for b in content or []:
        t = b.get("type")
        if t == "text":
            parts.append({"type": "text", "text": b.get("text", "")})
        elif t == "image":
            src = b.get("source") or {}
            if src.get("type") == "base64":
                url = f"data:{src.get('media_type','image/png')};base64,{src.get('data','')}"
            else:
                url = src.get("url", "")
            parts.append({"type": "image_url", "image_url": {"url": url}})
        elif t == "tool_use":
            calls.append(b)
        elif t == "tool_result":
            # OpenAI carries a tool result as its own message, not as a block
            inner = b.get("content")
            if isinstance(inner, list):
                inner = " ".join(x.get("text", "") for x in inner if isinstance(x, dict))
            tool_results.append({"role": "tool", "tool_call_id": b.get("tool_use_id"),
                                 "content": inner if isinstance(inner, str) else json.dumps(inner)})
    # a single text part is plain text — some providers are stricter about that
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0]["text"], tool_results, calls
    return parts, tool_results, calls


def to_openai(body):
    """Anthropic request → OpenAI request. Returns (openai_body, notes)."""
    notes = [f for f in DROPPED_SILENTLY_WOULD_BE_WORSE if _mentions(body, f)]
    msgs = []

    system = body.get("system")
    if system:
        if isinstance(system, list):
            system = " ".join(b.get("text", "") for b in system if isinstance(b, dict))
        msgs.append({"role": "system", "content": system})

    for m in body.get("messages") or []:
        # An assistant turn carries its tool calls as blocks alongside text;
        # OpenAI puts them in a sibling field instead. Reading them out of the
        # SAME pass that builds the content is what keeps the two in step — a
        # second walk over the blocks was where this crashed the first time a
        # real client sent one back.
        content, tool_results, calls = _content_to_openai(m.get("content"))
        entry = {"role": m.get("role", "user")}
        if calls:
            entry["content"] = content or None
            entry["tool_calls"] = [{
                "id": c.get("id"), "type": "function",
                "function": {"name": c.get("name"),
                             "arguments": json.dumps(c.get("input") or {})},
            } for c in calls]
        else:
            entry["content"] = content
        if entry.get("content") or entry.get("tool_calls"):
            msgs.append(entry)
        msgs.extend(tool_results)

    out = {"model": body.get("model"), "messages": msgs,
           "max_tokens": body.get("max_tokens")}
    for a, b in (("temperature", "temperature"), ("top_p", "top_p"),
                 ("stop_sequences", "stop"), ("stream", "stream")):
        if body.get(a) is not None:
            out[b] = body[a]

    if body.get("tools"):
        out["tools"] = [{"type": "function", "function": {
            "name": t.get("name"), "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object"},
        }} for t in body["tools"]]
    tc = body.get("tool_choice") or {}
    if tc.get("type") == "any":
        out["tool_choice"] = "required"
    elif tc.get("type") == "tool" and tc.get("name"):
        out["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
    elif tc.get("type") == "auto":
        out["tool_choice"] = "auto"
    return out, notes


def _mentions(obj, key):
    if isinstance(obj, dict):
        return key in obj or any(_mentions(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_mentions(v, key) for v in obj)
    return False


def from_openai(d, model):
    """OpenAI response → Anthropic response."""
    choice = (d.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    blocks = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for c in msg.get("tool_calls") or []:
        fn = c.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"_unparsed": fn.get("arguments")}
        blocks.append({"type": "tool_use", "id": c.get("id"),
                       "name": fn.get("name"), "input": args})
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    u = d.get("usage") or {}
    return {
        "id": d.get("id") or f"msg_{int(time.time()*1000)}",
        "type": "message", "role": "assistant", "model": model,
        "content": blocks,
        "stop_reason": STOP_REASON.get(choice.get("finish_reason"), "end_turn"),
        "stop_sequence": None,
        "usage": {"input_tokens": u.get("prompt_tokens") or 0,
                  "output_tokens": u.get("completion_tokens") or 0},
    }


class StreamTranslator:
    """OpenAI's SSE deltas → Anthropic's event sequence.

    The two are not the same stream with different names. Anthropic frames a
    response as a message that opens, one or more content blocks that open,
    delta and close, then a message that closes — and a client written against
    it will hang or throw if those frames do not arrive in that order. So this
    is a state machine, not a field rename.
    """

    def __init__(self, model):
        self.model = model
        self.open_block = False
        self.started = False
        self.stop_reason = "end_turn"
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.msg_id = f"msg_{int(time.time()*1000)}"

    @staticmethod
    def _ev(name, data):
        return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()

    def start(self):
        self.started = True
        return self._ev("message_start", {"type": "message_start", "message": {
            "id": self.msg_id, "type": "message", "role": "assistant",
            "model": self.model, "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": self.usage}})

    def feed(self, line):
        """One `data:` line from upstream → zero or more Anthropic events."""
        out = b""
        payload = line[5:].strip() if line.startswith("data:") else ""
        if not payload:
            return out
        if payload == "[DONE]":
            return out + self.finish()
        try:
            d = json.loads(payload)
        except json.JSONDecodeError:
            return out
        if not self.started:
            out += self.start()
        u = d.get("usage") or {}
        if u:
            self.usage = {"input_tokens": u.get("prompt_tokens") or 0,
                          "output_tokens": u.get("completion_tokens") or 0}
        ch = (d.get("choices") or [{}])[0]
        delta = ch.get("delta") or {}
        text = delta.get("content")
        if text:
            if not self.open_block:
                out += self._ev("content_block_start", {
                    "type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""}})
                self.open_block = True
            out += self._ev("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": text}})
        if ch.get("finish_reason"):
            self.stop_reason = STOP_REASON.get(ch["finish_reason"], "end_turn")
        return out

    def finish(self):
        out = b""
        if not self.started:
            out += self.start()
        if self.open_block:
            out += self._ev("content_block_stop",
                            {"type": "content_block_stop", "index": 0})
            self.open_block = False
        out += self._ev("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self.stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": self.usage["output_tokens"]}})
        out += self._ev("message_stop", {"type": "message_stop"})
        return out
