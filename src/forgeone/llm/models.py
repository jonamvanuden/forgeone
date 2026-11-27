"""Model factory: one place that turns a model name into a chat model.

The model's provider (llm/providers.py) picks the adapter: anthropic -> ChatAnthropic,
openai_compat -> OpenAICompatChat (DeepInfra, RunPod, OpenAI, any vLLM server).
"""
from __future__ import annotations

from ..config import settings
from .registry import model_info

ADAPTERS = {}


def adapter(name: str):
    """Register a new adapter: @adapter("my_api") def make(info, max_tokens): return chat."""
    def deco(fn):
        ADAPTERS[name] = fn
        return fn
    return deco


@adapter("anthropic")
def _anthropic(info, max_tokens):
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=info.provider_model, max_tokens=max_tokens or info.max_output)


@adapter("openai_compat")
def _openai_compat(info, max_tokens):
    from .openai_compat import OpenAICompatChat

    return OpenAICompatChat(info, max_tokens=max_tokens)


def chat_model(name: str | None = None, max_tokens: int | None = None):
    """Return a chat model. Newest Claude models reject a custom temperature, so none is set."""
    info = model_info(name or settings().builder_model)
    make = ADAPTERS.get(info.adapter)
    if make is None:
        raise ValueError(f"model {info.name}: provider {info.provider} uses unknown adapter {info.adapter!r}")
    return make(info, max_tokens)
