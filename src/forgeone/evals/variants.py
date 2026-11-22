"""Variants: what is being compared in an experiment (model, budget, tools, prompt addition).

Defined by the owner in the agent repo, evals/variants.yaml:

    variants:
      - name: haiku
        model: claude-haiku-5-5
      - name: sonnet
        model: claude-sonnet-5-5
      - name: haiku-lean
        model: claude-haiku-5-5
        max_steps: 15
        tools: [web_search, open_page, write_report]
      - name: qwen3                     # a model from models.yaml, e.g. Qwen3 on RunPod serverless
        model: qwen3-8b
      - name: haiku-lean-context        # same model, less context: lean | default | rich, or overrides
        model: claude-haiku-5-5
        context: {base: lean, keep_full: 2}

Change ONE thing per variant, so the results say what made the difference.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from ..config import settings
from ..spec.tools import ToolName


class Variant(BaseModel):
    name: str
    model: str | None = Field(default=None, description="None: FORGEONE_AGENT_MODEL")
    max_steps: int | None = Field(default=None, ge=1, le=200)
    max_cost_usd: float | None = Field(default=None, gt=0)
    tools: list[ToolName] | None = None
    prompt_suffix: str = ""
    context: str | dict | None = Field(default=None, description="context policy: lean | default | rich | overrides")

    @field_validator("context")
    @classmethod
    def _context(cls, v):
        from ..context import resolve

        resolve(v)                      # fails early on unknown presets or settings
        return v

    @property
    def context_policy(self):
        from ..context import resolve

        return resolve(self.context)

    @property
    def resolved_model(self) -> str:
        return self.model or settings().agent_model

    @property
    def fingerprint(self) -> str:
        blob = yaml.safe_dump({**self.model_dump(), "model": self.resolved_model}, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:10]


DEFAULT = Variant(name="default")


def load_variants(path: Path) -> list[Variant]:
    if not path.exists():
        return [DEFAULT]
    raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("variants") or []
    out = [Variant.model_validate(v) for v in raw]
    names = [v.name for v in out]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate variant names in {path}")
    return out or [DEFAULT]
