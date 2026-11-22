"""Every model call, logged automatically through a LangChain callback."""
from __future__ import annotations

import logging
import time

from langchain_core.callbacks import BaseCallbackHandler

from .events import log_event, write_payload


class LLMCallLogger(BaseCallbackHandler):
    """Pass in config={"callbacks": [LLMCallLogger()]}; logs every model call
    with model, latency, tokens and the full prompt/response."""

    def __init__(self):
        self._calls: dict[str, dict] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        params = kwargs.get("invocation_params") or {}
        model = params.get("model") or params.get("model_name") or (kwargs.get("metadata") or {}).get("ls_model_name")
        prompt = [{"role": m.type, "content": m.content} for batch in messages for m in batch]
        prompt_data = {"model": model, "params": params, "messages": prompt}
        ref = write_payload("prompt", prompt_data)
        self._calls[str(run_id)] = {"t0": time.perf_counter(), "model": model,
                                    "prompt_ref": ref, "prompt": prompt_data}
        log_event("llm.request", model=model, messages=len(prompt),
                  prompt_chars=sum(len(str(m["content"])) for m in prompt), payload=ref)

    def on_llm_end(self, response, *, run_id, **kwargs):
        call = self._calls.pop(str(run_id), {})
        gen = response.generations[0][0] if response.generations and response.generations[0] else None
        msg = getattr(gen, "message", None)
        usage = (getattr(msg, "usage_metadata", None) or {}) if msg else {}
        out = {
            "content": getattr(msg, "content", getattr(gen, "text", None)),
            "tool_calls": getattr(msg, "tool_calls", None),
            "stop_reason": (getattr(msg, "response_metadata", {}) or {}).get("stop_reason"),
        }
        ref = write_payload("response", out)
        log_event("llm.response",
                  model=call.get("model"),
                  latency_ms=round((time.perf_counter() - call.get("t0", time.perf_counter())) * 1000),
                  input_tokens=usage.get("input_tokens"),
                  output_tokens=usage.get("output_tokens"),
                  cache_read_tokens=(usage.get("input_token_details") or {}).get("cache_read"),
                  cache_write_tokens=(usage.get("input_token_details") or {}).get("cache_creation"),
                  stop_reason=out["stop_reason"],
                  prompt_payload=call.get("prompt_ref"), payload=ref,
                  _blobs={"prompt": call.get("prompt"), "response": out})

    def on_llm_error(self, error, *, run_id, **kwargs):
        call = self._calls.pop(str(run_id), {})
        log_event("llm.error", logging.ERROR, model=call.get("model"),
                  error=f"{type(error).__name__}: {error}", error_type=type(error).__name__,
                  latency_ms=round((time.perf_counter() - call.get("t0", time.perf_counter())) * 1000),
                  prompt_payload=call.get("prompt_ref"),
                  _blobs={"prompt": call.get("prompt")})
