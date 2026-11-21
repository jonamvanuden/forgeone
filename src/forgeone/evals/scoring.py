"""From check results to a scorecard: what a version is worth, per category, with honesty about noise."""
from __future__ import annotations

from collections import defaultdict
from statistics import mean

from pydantic import BaseModel, Field


class CheckResult(BaseModel):
    name: str = ""
    passed: bool
    hard: bool = False
    detail: str = ""


class CaseResult(BaseModel):
    case_id: str
    category: str
    repeat: int = 0
    checks: list[CheckResult] = Field(default_factory=list)
    judge_scores: dict[str, float] = Field(default_factory=dict, description="rubric dimension -> 1..5")
    failure_tags: list[str] = Field(default_factory=list)
    cost_usd: float | None = None
    duration_ms: int | None = None

    @property
    def hard_failures(self) -> list[str]:
        return [c.name for c in self.checks if c.hard and not c.passed]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


class Scorecard(BaseModel):
    cases: int
    runs: int
    pass_rate: float = Field(description="share of runs where every check passed")
    pass_all_repeats: float = Field(description="pass^k: share of cases passed in EVERY repeat")
    hard_failures: list[str] = Field(default_factory=list, description="'case_id: check' entries")
    by_category: dict[str, float] = Field(default_factory=dict)
    judge_means: dict[str, float] = Field(default_factory=dict)
    failure_tags: dict[str, int] = Field(default_factory=dict)
    mean_cost_usd: float | None = None


def scorecard(results: list[CaseResult]) -> Scorecard:
    if not results:
        raise ValueError("no results")
    by_case: dict[str, list[CaseResult]] = defaultdict(list)
    by_cat: dict[str, list[bool]] = defaultdict(list)
    judge: dict[str, list[float]] = defaultdict(list)
    tags: dict[str, int] = defaultdict(int)
    hard: list[str] = []
    for r in results:
        by_case[r.case_id].append(r)
        by_cat[r.category].append(r.passed)
        for k, v in r.judge_scores.items():
            judge[k].append(v)
        for t in r.failure_tags:
            tags[t] += 1
        hard += [f"{r.case_id}: {h}" for h in r.hard_failures]
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    return Scorecard(
        cases=len(by_case),
        runs=len(results),
        pass_rate=mean(r.passed for r in results),
        pass_all_repeats=mean(all(x.passed for x in rs) for rs in by_case.values()),
        hard_failures=sorted(set(hard)),
        by_category={k: mean(v) for k, v in sorted(by_cat.items())},
        judge_means={k: mean(v) for k, v in sorted(judge.items())},
        failure_tags=dict(sorted(tags.items(), key=lambda kv: -kv[1])),
        mean_cost_usd=mean(costs) if costs else None,
    )
