"""Model factory. Claude by default; any LangChain-supported provider via "provider:model"."""
from __future__ import annotations

from ..config import settings


def chat_model(name: str | None = None, max_tokens: int = 8000):
    """Return a LangChain chat model. Newest Claude models reject a custom temperature,
    so none is set here."""
    name = name or settings().builder_model
    if name.startswith("claude"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=name, max_tokens=max_tokens)
    from langchain.chat_models import init_chat_model

    return init_chat_model(name, max_tokens=max_tokens)
