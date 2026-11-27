"""Chat model adapter for OpenAI-compatible endpoints: RunPod serverless vLLM (e.g. Qwen3) and
any other server that speaks /chat/completions.

It behaves like the LangChain chat models the rest of ForgeOne uses: `invoke` returns an
AIMessage (content, tool_calls, usage_metadata), `bind_tools` takes ForgeOne's tool schemas,
`with_structured_output` returns validated Pydantic objects, and LangChain callbacks (the LLM
call logger) see every call. Only the standard library is used for HTTP.

RunPod notes:
- base URL: https://api.runpod.ai/v2/<ENDPOINT_ID>/openai/v1, key in RUNPOD_API_KEY
- tool calls need the endpoint's ENABLE_AUTO_TOOL_CHOICE=true and TOOL_CALL_PARSER=hermes (Qwen3)
- serverless workers start cold: the first call can take minutes, hence the long timeout
- Qwen3 thinks before answering unless told not to; `thinking: false` sends enable_thinking=false,
  and any <think>...</think> block is removed from the content (kept in response_metadata)
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from types import SimpleNamespace
from typing import Any

from .registry import ModelInfo

THINK = re.compile(r"<think>(.*?)</think>\s*", re.S)
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content or [])


def _role(m) -> str:
    t = getattr(m, "type", None) or type(m).__name__.replace("Message", "").lower()
    return {"human": "user", "system": "system", "ai": "assistant", "tool": "tool"}.get(t, t)


def to_openai_messages(messages) -> list[dict]:
    out = []
    for m in messages:
        role = _role(m)
        if role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": _text(m.content) or None}
            calls = getattr(m, "tool_calls", None) or []
            if calls:
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": json.dumps(c.get("args") or {})}}
                                     for c in calls]
            out.append(msg)
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": _text(m.content)})
        else:
            out.append({"role": role, "content": _text(m.content)})
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    """ForgeOne tool schemas use Anthropic's shape (name, description, input_schema)."""
    out = []
    for t in tools:
        if t.get("type") == "function":
            out.append(t)
        else:
            out.append({"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                         "parameters": t.get("input_schema") or {"type": "object"}}})
    return out


class OpenAICompatChat:
    """invoke / bind_tools / with_structured_output over an OpenAI-compatible endpoint."""

    def __init__(self, info: ModelInfo, max_tokens: int | None = None, tools: list[dict] | None = None,
                 transport=None):
        self.info = info
        self.max_tokens = max_tokens or info.max_output
        self.tools = tools
        self._transport = transport or self._http

    # ---------------------------------------------------------------- LangChain-like API
    def bind_tools(self, tools: list[dict], **_) -> "OpenAICompatChat":
        return OpenAICompatChat(self.info, self.max_tokens, to_openai_tools(tools), self._transport)

    def with_structured_output(self, schema, method: str = "json_schema", include_raw: bool = False, **_):
        return _Structured(self, schema, include_raw)

    def invoke(self, messages, config: dict | None = None, *, response_format: dict | None = None,
               system_extra: str | None = None):
        from langchain_core.messages import HumanMessage, SystemMessage

        msgs = [HumanMessage(messages)] if isinstance(messages, str) else list(messages)
        if system_extra:
            msgs = [SystemMessage(system_extra)] + msgs
        callbacks = (config or {}).get("callbacks") or []
        run_id = uuid.uuid4()
        params = {"model": self.info.name, "provider": self.info.provider, "provider_model": self.info.provider_model}
        for cb in callbacks:
            cb.on_chat_model_start({"name": "OpenAICompatChat"}, [msgs], run_id=run_id, invocation_params=params,
                                   metadata={"ls_model_name": self.info.name})
        try:
            ai = self._call(msgs, response_format)
        except Exception as e:
            for cb in callbacks:
                cb.on_llm_error(e, run_id=run_id)
            raise
        result = SimpleNamespace(generations=[[SimpleNamespace(message=ai, text=_text(ai.content))]], llm_output=None)
        for cb in callbacks:
            cb.on_llm_end(result, run_id=run_id)
        return ai

    # ---------------------------------------------------------------- the request
    def _body(self, msgs, response_format) -> dict:
        body: dict[str, Any] = {"model": self.info.provider_model, "messages": to_openai_messages(msgs),
                                "max_tokens": self.max_tokens}
        if self.tools:
            body["tools"], body["tool_choice"] = self.tools, "auto"
        if response_format:
            body["response_format"] = response_format
        if self.info.prov.send_thinking_flag and "qwen3" in self.info.provider_model.lower():
            body["chat_template_kwargs"] = {"enable_thinking": bool(self.info.thinking)}
        return body

    def _http(self, url: str, body: dict, headers: dict, timeout: float) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise ProviderError(f"{self.info.name}: HTTP {e.code}: {detail}", e.code) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ProviderError(f"{self.info.name}: {e}", None) from e

    def _call(self, msgs, response_format):
        from langchain_core.messages import AIMessage

        key = os.getenv(self.info.key_env)
        if not key:
            raise ProviderError(f"{self.info.key_env} is not set (needed for model {self.info.name})")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        body = self._body(msgs, response_format)
        for attempt in range(self.info.max_retries + 1):
            t0 = time.perf_counter()
            try:
                data = self._transport(f"{self.info.url}/chat/completions", body, headers, self.info.timeout_s)
                break
            except ProviderError as e:
                retry = e.status is None or e.status in RETRY_STATUS
                if not retry or attempt == self.info.max_retries:
                    raise
                time.sleep(min(30, 2 * 2 ** attempt))
        if data.get("error"):
            raise ProviderError(f"{self.info.name}: {data['error']}")
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or "\n".join(THINK.findall(content)) or None
        content = THINK.sub("", content).strip()
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                args = {"_invalid_arguments": raw}
            calls.append({"name": fn.get("name"), "args": args, "id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                          "type": "tool_call"})
        u = data.get("usage") or {}
        tin, tout = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        return AIMessage(content, tool_calls=calls,
                         usage_metadata={"input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout},
                         response_metadata={"model_name": self.info.name, "stop_reason": choice.get("finish_reason"),
                                            "reasoning": reasoning, "latency_ms": round((time.perf_counter() - t0) * 1000)})


class _Structured:
    """Structured output: vLLM's guided decoding (response_format json_schema), or the schema in
    the prompt for servers without it. Always validated with Pydantic."""

    def __init__(self, chat: OpenAICompatChat, schema, include_raw: bool):
        self.chat, self.schema, self.include_raw = chat, schema, include_raw

    def invoke(self, prompt, config: dict | None = None):
        js = self.schema.model_json_schema()
        mode = self.chat.info.structured_mode
        if mode == "json_schema":
            ai = self.chat.invoke(prompt, config, response_format={
                "type": "json_schema", "json_schema": {"name": self.schema.__name__, "schema": js}})
        elif mode == "json_object":     # JSON mode: valid JSON guaranteed, the schema comes from the prompt
            ai = self.chat.invoke(prompt, config, response_format={"type": "json_object"}, system_extra=(
                "Answer with one JSON object that matches this JSON schema, and nothing else:\n" + json.dumps(js)))
        else:
            ai = self.chat.invoke(prompt, config, system_extra=(
                "Answer with one JSON object that matches this JSON schema, and nothing else:\n" + json.dumps(js)))
        parsed, error = None, None
        try:
            parsed = self.schema.model_validate(json.loads(FENCE.sub("", _text(ai.content).strip())))
        except Exception as e:  # noqa: BLE001
            error = e
        if self.include_raw:
            return {"raw": ai, "parsed": parsed, "parsing_error": error}
        if error:
            raise ValueError(f"{self.chat.info.name}: output did not match {self.schema.__name__}: {error}")
        return parsed
