"""Providers: where a model runs and how ForgeOne talks to it.

Two adapters do the talking:
    anthropic      langchain_anthropic.ChatAnthropic (Claude)
    openai_compat  llm/openai_compat.OpenAICompatChat (anything with /chat/completions)

A provider is an adapter plus the facts that differ per vendor: base URL, which env var holds
the key, how structured output works there. Adding a vendor is one entry in PROVIDERS (or a
`providers:` section in models.yaml); nothing else changes.

    # models.yaml
    providers:
      together:                              # a provider of your own
        adapter: openai_compat
        base_url: https://api.together.xyz/v1
        key_env: TOGETHER_API_KEY
        structured: json_object
"""
from __future__ import annotations

from pydantic import BaseModel


class Provider(BaseModel):
    name: str
    adapter: str                         # anthropic | openai_compat
    base_url: str | None = None          # may contain {endpoint_id}
    key_env: str | None = None
    structured: str = "json_schema"      # default for its models: json_schema | json_object | prompt
    send_thinking_flag: bool = False     # vLLM: chat_template_kwargs.enable_thinking for Qwen3
    note: str = ""


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider(name="anthropic", adapter="anthropic", key_env="ANTHROPIC_API_KEY",
                          note="Claude; built-in models need no entry"),
    "deepinfra": Provider(name="deepinfra", adapter="openai_compat",
                          base_url="https://api.deepinfra.com/v1/openai", key_env="DEEPINFRA_API_KEY",
                          structured="json_object", note="open models, pay per token"),
    "runpod": Provider(name="runpod", adapter="openai_compat",
                       base_url="https://api.runpod.ai/v2/{endpoint_id}/openai/v1", key_env="RUNPOD_API_KEY",
                       send_thinking_flag=True, note="your own vLLM serverless endpoint (needs endpoint_id)"),
    "openai": Provider(name="openai", adapter="openai_compat", base_url="https://api.openai.com/v1",
                       key_env="OPENAI_API_KEY"),
    "openai_compatible": Provider(name="openai_compatible", adapter="openai_compat", key_env="OPENAI_API_KEY",
                                  send_thinking_flag=True, note="any other server; set base_url per model"),
}


def providers(extra: dict | None = None) -> dict[str, Provider]:
    """Built-in providers plus the `providers:` section of models.yaml (which may override them)."""
    out = dict(PROVIDERS)
    for name, cfg in (extra or {}).items():
        base = out[name].model_dump() if name in out else {}
        out[name] = Provider(**{**base, **(cfg or {}), "name": name})
    return out


def provider(name: str) -> Provider:
    from .registry import all_providers

    ps = all_providers()
    if name not in ps:
        raise KeyError(f"unknown provider {name!r} (known: {', '.join(sorted(ps))})")
    return ps[name]
