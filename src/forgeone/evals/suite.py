"""The eval suite: a fixed set of tasks per agent, stored in the AGENT'S OWN repo at evals/suite.yaml.

Rule: the builder never sees this file. Builder-written test cases are for fast feedback
during a build; only this suite decides whether a version is promoted (Goodhart's law).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


Category = Literal["factual", "comparison", "recency", "trap", "conflicting", "navigation", "safety"]


class EvalCase(BaseModel):
    id: str = Field(description="Stable id, e.g. 'trap-nonexistent-study'. Never reuse an id.")
    category: Category
    task: str = Field(description="The research command given to the agent.")
    checks: list[str] = Field(default_factory=list, description="Programmatic checks to run (see checks/).")
    reference: str = Field(default="", description="Notes for the judge: what a correct answer contains.")
    expect_not_found: bool = Field(default=False, description="Trap task: the honest answer is 'not found'.")
    max_cost_usd: float | None = Field(default=None, description="Hard budget for one run of this case.")

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not v or any(c.isspace() for c in v):
            raise ValueError("id must be non-empty without spaces")
        return v


class EvalSuite(BaseModel):
    agent: str
    description: str = ""
    repeats: int = Field(default=3, ge=1, le=10, description="Runs per case; noise needs repeats.")
    cases: list[EvalCase] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def _unique(cls, cases: list[EvalCase]) -> list[EvalCase]:
        ids = [c.id for c in cases]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate case ids: {sorted(dupes)}")
        return cases

    @property
    def version(self) -> str:
        """Content hash: results are only comparable on the same suite version."""
        blob = yaml.safe_dump(self.model_dump(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def by_category(self) -> dict[str, list[EvalCase]]:
        out: dict[str, list[EvalCase]] = {}
        for c in self.cases:
            out.setdefault(c.category, []).append(c)
        return out


def suite_path(agent: str) -> Path:
    from ..workspace import AgentRepo

    return AgentRepo.for_agent(agent).suite_path


def load_suite(agent: str | None = None, path: str | Path | None = None) -> EvalSuite:
    p = Path(path) if path else suite_path(agent or "voyagerone")
    suite = EvalSuite.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
    from .checks import CHECKS  # validate check names early

    unknown = sorted({c for case in suite.cases for c in case.checks} - set(CHECKS))
    if unknown:
        raise ValueError(f"unknown checks in {p}: {unknown}. Known: {sorted(CHECKS)}")
    return suite
