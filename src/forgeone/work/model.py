"""The work model: Goal -> Milestones -> Tasks -> Actions.

The same for every kind of agent (builder, discovery, content): a goal is broken into
milestones, a milestone into tasks, and every task goes through stages. Each attempt at a
stage is an Action, so every decision, retry and verdict is a database row.

Fields set at planning time (the prediction) and fields filled while running (the outcome)
sit side by side, so predicted vs. actual can be compared per task type.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

AgentType = Literal["builder", "discovery", "content", "generic"]
Autonomy = Literal["supervised", "autonomous"]
TaskType = Literal["research", "read", "extract", "analyze", "write", "code", "review", "decide", "other"]
Risk = Literal["low", "medium", "high"]
Stage = Literal["execute", "verify", "edge_test", "approve"]
ActionKind = Literal["plan", "execute", "verify", "edge_test", "approve"]
Executor = Literal["agent", "llm", "step", "none"]   # step: a registered function of a playbook
Verifier = Literal["model", "checks"]                 # checks: decided by automatic checks only
Approval = Literal["human", "ai", "none"]
ActorType = Literal["ai", "human", "tool"]
FailureReason = Literal["tool_error", "wrong_source", "criteria_not_met", "budget_exceeded", "hallucination",
                        "blocked_dependency", "timeout", "rejected", "model_error", "other"]

GoalStatus = Literal["planned", "running", "waiting", "paused", "done", "failed", "cancelled"]
MilestoneStatus = Literal["pending", "running", "waiting", "done", "failed", "superseded"]
TaskStatus = Literal["pending", "running", "waiting", "done", "failed", "blocked", "skipped", "superseded"]
ActionStatus = Literal["running", "passed", "failed", "pending"]   # pending = waiting for a human

TASK_TYPES: dict[str, str] = {
    "research": "find information on the web from several sources",
    "read": "open and read specific pages or documents",
    "extract": "pull specific facts or data out of sources",
    "analyze": "compare, calculate or reason over material already gathered",
    "write": "produce text: a report, summary, section, message",
    "code": "write or change code or configuration",
    "review": "check someone else's output against criteria",
    "decide": "choose between options and justify the choice",
    "other": "anything that fits none of the above",
}
FAILURE_REASONS: dict[str, str] = {
    "tool_error": "a tool failed (page did not load, search failed)",
    "wrong_source": "used a source that does not support the claim",
    "criteria_not_met": "the output misses acceptance criteria",
    "budget_exceeded": "ran out of cost or step budget",
    "hallucination": "claims something no source supports",
    "blocked_dependency": "a task it depends on failed",
    "timeout": "took longer than allowed",
    "rejected": "an approver rejected it",
    "model_error": "the model call itself failed",
    "other": "anything else",
}
OPEN = ("pending", "running", "waiting")


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


class Goal(BaseModel):
    id: str = Field(default_factory=lambda: new_id("G"))
    title: str
    description: str = ""
    agent: str | None = None                 # registered agent that executes tool tasks (voyagerone)
    agent_type: AgentType = "generic"
    success_criteria: list[str] = []
    constraints: list[str] = []
    budget_usd: float | None = None
    deadline: datetime | None = None
    autonomy: Autonomy = "supervised"
    playbook: str | None = None              # fixed plan for a known process (e.g. build_agent); None: planner model
    playbook_args: dict = {}
    status: GoalStatus = "planned"
    note: str | None = None                  # why it waits, pauses or failed
    plan_version: int = 0
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=now)


class Milestone(BaseModel):
    id: str = Field(default_factory=lambda: new_id("M"))
    goal_id: str
    plan_version: int
    seq: int
    title: str
    description: str = ""
    done_when: list[str] = []
    approval: Approval | None = None         # None: decided by the goal's autonomy
    status: MilestoneStatus = "pending"
    note: str | None = None
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=now)


class Task(BaseModel):
    id: str = Field(default_factory=lambda: new_id("T"))
    goal_id: str
    milestone_id: str
    parent_id: str | None = None             # set for subtasks (depth 2)
    plan_version: int
    seq: int
    code: str                                # "2.3" or "2.3.1": readable position in the plan
    depth: int = 1
    # -- what to do (planning time)
    title: str
    description: str = ""
    type: TaskType = "other"
    difficulty: int = 3                      # predicted, 1..5
    risk: Risk = "low"
    reversible: bool = True
    acceptance_criteria: list[str] = []
    depends_on: list[str] = []               # task ids
    input: str = ""
    expected_output: str = ""
    # -- how (routing + policy, planning time)
    executor: Executor = "llm"
    step: str | None = None                  # for executor "step": the playbook function that does the work
    verifier: Verifier = "model"
    model: str | None = None
    routing_reason: str = ""
    stages: list[Stage] = []                 # empty for a container task with subtasks
    approver: ActorType | None = None        # who approves, if approve is a stage
    max_attempts: int = 3
    budget_usd: float | None = None          # per attempt
    timeout_s: int | None = None
    est_steps: int | None = None
    est_minutes: float | None = None
    est_cost_usd: float | None = None
    # -- outcome (filled while running)
    status: TaskStatus = "pending"
    attempts: int = 0
    failure_reason: FailureReason | None = None
    failure_detail: str | None = None
    feedback: str | None = None              # what the last verifier/approver asked to fix
    output: str | None = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    actual_difficulty: float | None = None
    created_at: datetime = Field(default_factory=now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=now)


class Action(BaseModel):
    id: str = Field(default_factory=lambda: new_id("A"))
    goal_id: str
    milestone_id: str | None = None
    task_id: str | None = None
    kind: ActionKind
    attempt: int = 1
    actor_type: ActorType = "ai"
    actor: str = ""                          # model name, tool name or the human's name
    status: ActionStatus = "running"
    confidence: float | None = None
    reason: str | None = None
    feedback: str | None = None
    failure_reason: FailureReason | None = None
    criteria: list[dict] = []                # per-criterion results: {criterion, met, note}
    input: dict = {}                         # the context this action was given (summary)
    output: dict = {}
    prompt_version: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    run_id: str | None = None
    started_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None


class GoalTree(BaseModel):
    """A goal with everything under it, as loaded from the store."""
    goal: Goal
    milestones: list[Milestone] = []
    tasks: list[Task] = []
    actions: list[Action] = []

    # -- navigation helpers (current plan only unless stated)
    def active_milestones(self) -> list[Milestone]:
        return sorted((m for m in self.milestones if m.status != "superseded"), key=lambda m: m.seq)

    def tasks_of(self, milestone_id: str) -> list[Task]:
        return sorted((t for t in self.tasks if t.milestone_id == milestone_id and t.status != "superseded"),
                      key=lambda t: [int(x) for x in t.code.split(".")])

    def children(self, task_id: str) -> list[Task]:
        return [t for t in self.tasks if t.parent_id == task_id and t.status != "superseded"]

    def task(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def milestone(self, milestone_id: str) -> Milestone | None:
        return next((m for m in self.milestones if m.id == milestone_id), None)

    def actions_of(self, task_id: str) -> list[Action]:
        return sorted((a for a in self.actions if a.task_id == task_id), key=lambda a: a.started_at)

    def leaf_tasks(self) -> list[Task]:
        return [t for t in self.tasks if t.status != "superseded" and t.stages]
