"""Which models ForgeOne can use, where they run, and which model does which job.

Claude is built in. Everything else comes from models.yaml in the ForgeOne root:

    models:
      qwen3-8b:                         # the name used everywhere in ForgeOne
        provider: runpod                # see llm/providers.py: anthropic | deepinfra | runpod | openai | openai_compatible
        endpoint_id: abc123xyz
        model: Qwen/Qwen3-8B            # the id the provider knows it by
        price_in: 0.05                  # USD per million tokens
        price_out: 0.20
      llama-70b:
        provider: deepinfra
        model: meta-llama/Llama-3.3-70B-Instruct
        price_in: 0.23
        price_out: 0.40

    routing:                            # which model does which job (all optional)
      planner: claude-sonnet-5-5        # plans goals, milestones, tasks
      judge: claude-haiku-5-5           # verify, edge_test, approve
      cheap: llama-70b                  # tasks of difficulty 1..3
      strong: claude-sonnet-5-5         # tasks of difficulty 4..5
      types:                            # per task type; beats cheap/strong
        write: deepinfra:Qwen/Qwen3-235B-A22B-Instruct-2507

A model can also be named without an entry as `provider:model-id`, e.g.
`deepinfra:meta-llama/Llama-3.3-70B-Instruct` (no price known, so register it to get costs).
Keys come from the environment (.env), never from this file.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from ..config import settings
from .providers import Provider, providers


class ModelInfo(BaseModel):
    name: str
    provider: str = "anthropic"            # a name from llm/providers.py or models.yaml providers:
    model: str | None = None               # id at the provider; default: name
    endpoint_id: str | None = None         # runpod
    base_url: str | None = None            # overrides the provider's
    api_key_env: str | None = None
    context_window: int = 200_000
    max_output: int = 8000
    price_in: float | None = None          # USD per million tokens; None: use llm/pricing.PRICES
    price_out: float | None = None
    thinking: bool = False
    structured: str | None = None          # json_schema | json_object | prompt; None: the provider's
    timeout_s: float = 300
    max_retries: int = 3

    @property
    def provider_model(self) -> str:
        return self.model or self.name

    @property
    def prov(self) -> Provider:
        from .providers import provider

        return provider(self.provider)

    @property
    def adapter(self) -> str:
        return self.prov.adapter

    @property
    def structured_mode(self) -> str:
        return self.structured or self.prov.structured

    @property
    def url(self) -> str:
        base = self.base_url or self.prov.base_url
        if not base:
            raise ValueError(f"model {self.name}: provider {self.provider} has no base_url; set base_url in models.yaml")
        if "{endpoint_id}" in base:
            if not self.endpoint_id:
                raise ValueError(f"model {self.name}: {self.provider} needs endpoint_id in models.yaml")
            base = base.replace("{endpoint_id}", self.endpoint_id)
        return base.rstrip("/")

    @property
    def key_env(self) -> str:
        return self.api_key_env or self.prov.key_env or "OPENAI_API_KEY"


CLAUDE = {
    "claude-haiku-5-5": ModelInfo(name="claude-haiku-5-5", context_window=200_000),
    "claude-sonnet-5-5": ModelInfo(name="claude-sonnet-5-5", context_window=200_000),
    "claude-opus-5-5": ModelInfo(name="claude-opus-5-5", context_window=200_000),
}


def models_file() -> Path:
    return settings().home / "models.yaml"


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _raw(path: Path | None = None) -> dict:
    p = path or models_file()
    return _load(str(p), p.stat().st_mtime) if p.exists() else {}


def all_providers(path: Path | None = None) -> dict[str, Provider]:
    return providers(_raw(path).get("providers"))


def registry(path: Path | None = None) -> dict[str, ModelInfo]:
    raw = _raw(path).get("models") or {}
    return {**CLAUDE, **{name: ModelInfo(name=name, **(cfg or {})) for name, cfg in raw.items()}}


def routing(path: Path | None = None) -> dict:
    """The `routing:` section: planner, judge, cheap, strong, types{task type: model}."""
    r = _raw(path).get("routing") or {}
    return {**r, "types": dict(r.get("types") or {})}


def model_info(name: str) -> ModelInfo:
    """Registered model, `provider:model-id`, or a bare claude-* name."""
    reg = registry()
    if name in reg:
        return reg[name]
    if ":" in name:
        prov, _, mid = name.partition(":")
        if prov in all_providers() and mid:
            return ModelInfo(name=name, provider=prov, model=mid)
    if name.startswith("claude"):
        return ModelInfo(name=name)
    raise KeyError(f"model {name!r} is not in {models_file()} and is not provider:model "
                   f"(known models: {', '.join(sorted(reg))}; providers: {', '.join(sorted(all_providers()))})")
