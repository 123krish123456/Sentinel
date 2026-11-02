"""Adapter that lets LLMClient talk to any OpenAI-compatible chat-completions endpoint.

Exposes `messages.create(...)` with the Anthropic call shape and returns an object shaped like an
Anthropic response, so the agents and LLMClient are unchanged. Translates tool schemas, tool calls
and tool results both ways.
"""
import json
import os
import time
from types import SimpleNamespace

import httpx

_STOP = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}


def _to_openai_messages(system: str, messages: list) -> list:
    out = [{"role": "system", "content": system}]
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
        elif m["role"] == "assistant":
            text = "".join(b["text"] for b in c if b["type"] == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                     for b in c if b["type"] == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user turn made of tool_result blocks
            for b in c:
                if b["type"] == "tool_result":
                    body = b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                "content": ("ERROR: " if b.get("is_error") else "") + body})
                elif b["type"] == "text":
                    out.append({"role": "user", "content": b["text"]})
    return out


def _to_anthropic(data: dict):
    choice = data["choices"][0]
    msg = choice["message"]
    blocks = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"],
                       "input": args})
    wrap = lambda b: SimpleNamespace(model_dump=lambda exclude_none=True, b=b: b, **b)
    u = data.get("usage") or {}
    return SimpleNamespace(
        content=[wrap(b) for b in blocks],
        stop_reason=_STOP.get(choice.get("finish_reason"), "end_turn"),
        usage=SimpleNamespace(input_tokens=u.get("prompt_tokens", 0),
                              output_tokens=u.get("completion_tokens", 0)))


class _Messages:
    def __init__(self, base_url: str, api_key: str, timeout: float = 180):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key, self.timeout = api_key, timeout

    def create(self, model, system, messages, tools, temperature=0, max_tokens=4096, tool_choice=None):
        body = {"model": model, "messages": _to_openai_messages(system, messages),
                "temperature": temperature, "max_tokens": max_tokens,
                "tools": [{"type": "function", "function": {
                    "name": t["name"], "description": t["description"],
                    "parameters": t["input_schema"]}} for t in tools]}
        if tool_choice and tool_choice.get("type") == "any":
            body["tool_choice"] = "required"
        elif tool_choice and tool_choice.get("name"):
            body["tool_choice"] = {"type": "function", "function": {"name": tool_choice["name"]}}
        for attempt in range(8):
            try:
                r = httpx.post(self.url, json=body, timeout=self.timeout,
                               headers={"Authorization": f"Bearer {self.key}"})
            except httpx.TransportError:  # timeouts, dropped connections
                if attempt == 7:
                    raise
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504, 520, 522, 524) and attempt < 7:
                ra = r.headers.get("retry-after", "0")
                time.sleep(max(float(ra) if ra.replace(".", "", 1).isdigit() else 0, 2 ** attempt))
                continue
            r.raise_for_status()
            return _to_anthropic(r.json())


class OpenAICompatAPI:
    def __init__(self, base_url: str, api_key_env: str):
        key = os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(f"Set the {api_key_env} environment variable.")
        self.messages = _Messages(base_url, key)
