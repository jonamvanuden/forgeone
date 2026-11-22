"""Model factory: one place that turns a model name into a chat model.

Claude models go to Anthropic; models registered in models.yaml with provider runpod or
openai_compatible (e.g. Qwen3 on RunPod serverless) go to the OpenAI-compatible adapter.
"""
from __future__ import annotations

from ..config import settings
from .registry import model_info


def chat_model(name: str | None = None, max_tokens: int | None = None):
    """Return a chat model. Newest Claude models reject a custom temperature, so none is set."""
    name = name or settings().builder_model
    info = model_info(name)
    if info.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=info.provider_model, max_tokens=max_tokens or info.max_output)
    if info.provider in ("runpod", "openai_compatible"):
        from .openai_compat import OpenAICompatChat

        return OpenAICompatChat(info, max_tokens=max_tokens)
    raise ValueError(f"model {name}: unknown provider {info.provider!r}")
