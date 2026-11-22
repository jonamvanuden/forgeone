"""Which models ForgeOne can use: Claude built in, everything else from models.yaml.

    # models.yaml, in the ForgeOne root (next to agents.yaml)
    models:
      qwen3-8b:                         # the name used everywhere in ForgeOne (variants, env, routing)
        provider: runpod                # runpod | openai_compatible
        endpoint_id: abc123xyz          # RunPod serverless endpoint id
        model: Qwen/Qwen3-8B            # the model id the endpoint serves
        context_window: 32768
        max_output: 4096
        price_in: 0.05                  # USD per million tokens (an estimate for GPU-second billing)
        price_out: 0.20
        thinking: false                 # Qwen3: disable the <think> phase (faster, cleaner JSON)
        structured: json_schema         # json_schema (vLLM guided decoding) | prompt
        timeout_s: 300                  # serverless cold starts can take minutes

The RunPod API key comes from RUNPOD_API_KEY (or api_key_env per model). Nothing secret
goes in models.yaml.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from ..config import settings


class ModelInfo(BaseModel):
    name: str
    provider: str = "anthropic"            # anthropic | runpod | openai_compatible
    model: str | None = None               # id at the provider; default: name
    endpoint_id: str | None = None         # runpod
    base_url: str | None = None            # openai_compatible (or to override runpod's)
    api_key_env: str | None = None
    context_window: int = 200_000
    max_output: int = 8000
    price_in: float | None = None          # USD per million tokens; None: use llm/pricing.PRICES
    price_out: float | None = None
    thinking: bool = False
    structured: str = "json_schema"
    timeout_s: float = 300
    max_retries: int = 3

    @property
    def provider_model(self) -> str:
        return self.model or self.name

    @property
    def url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.provider == "runpod":
            if not self.endpoint_id:
                raise ValueError(f"model {self.name}: runpod needs endpoint_id in models.yaml")
            return f"https://api.runpod.ai/v2/{self.endpoint_id}/openai/v1"
        raise ValueError(f"model {self.name}: no base_url")

    @property
    def key_env(self) -> str:
        return self.api_key_env or ("RUNPOD_API_KEY" if self.provider == "runpod" else "OPENAI_API_KEY")


CLAUDE = {
    "claude-haiku-5-5": ModelInfo(name="claude-haiku-5-5", context_window=200_000),
    "claude-sonnet-5-5": ModelInfo(name="claude-sonnet-5-5", context_window=200_000),
    "claude-opus-5-5": ModelInfo(name="claude-opus-5-5", context_window=200_000),
}


def models_file() -> Path:
    return settings().home / "models.yaml"


@lru_cache(maxsize=1)
def _load(path: str, mtime: float) -> dict[str, ModelInfo]:
    raw = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("models") or {}
    return {name: ModelInfo(name=name, **(cfg or {})) for name, cfg in raw.items()}


def registry(path: Path | None = None) -> dict[str, ModelInfo]:
    p = path or models_file()
    extra = _load(str(p), p.stat().st_mtime) if p.exists() else {}
    return {**CLAUDE, **extra}


def model_info(name: str) -> ModelInfo:
    """Known model, or a sensible default: claude-* is Anthropic, anything else must be registered."""
    reg = registry()
    if name in reg:
        return reg[name]
    if name.startswith("claude"):
        return ModelInfo(name=name)
    raise KeyError(f"model {name!r} is not in {models_file()} (known: {', '.join(sorted(reg))})")
