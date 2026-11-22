"""What the models must return (structured output). Kept separate from the stored records
in model.py: these are drafts and verdicts, the engine turns them into records."""
from __future__ import annotations


from pydantic import BaseModel, Field

from .model import FailureReason, Risk, TaskType


class MilestoneDraft(BaseModel):
    title: str = Field(description="the intermediate result, phrased as an outcome")
    description: str
    done_when: list[str] = Field(description="1 to 4 objectively checkable statements")


class MilestonePlan(BaseModel):
    reasoning: str = Field(description="two or three sentences on why this breakdown")
    milestones: list[MilestoneDraft]


class TaskDraft(BaseModel):
    title: str
    description: str
    type: TaskType
    difficulty: int = Field(description="1 trivial .. 3 normal .. 5 too big for one run")
    risk: Risk
    reversible: bool
    acceptance_criteria: list[str] = Field(description="2 to 5 statements checkable from the output alone")
    depends_on: list[int] = Field(description="1-based positions of earlier tasks in this list")
    input: str
    expected_output: str
    est_steps: int = Field(description="expected tool calls or model turns")
    est_minutes: float


class TaskPlan(BaseModel):
    tasks: list[TaskDraft]


class CriterionResult(BaseModel):
    criterion: str
    met: bool
    note: str


class Verdict(BaseModel):
    criteria: list[CriterionResult]
    passed: bool
    confidence: float = Field(description="0 to 1")
    reason: str
    failure_reason: FailureReason | None = None
    feedback: str = Field(description="concrete instructions for the next attempt; empty if passed")


class EdgeCase(BaseModel):
    case: str
    handled: bool
    note: str


class EdgeReport(BaseModel):
    cases: list[EdgeCase]
    passed: bool
    feedback: str


class Approval(BaseModel):
    approved: bool
    confidence: float = Field(description="0 to 1")
    reason: str
    feedback: str


