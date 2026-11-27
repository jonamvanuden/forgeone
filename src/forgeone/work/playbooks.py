"""Playbooks: fixed plans for processes ForgeOne knows how to do, such as building an agent.

A goal with a playbook skips the planner model: its milestones and tasks come from code, with
explicit stages, criteria and checks. Its tasks can be "steps": plain Python functions that do
the work (call a model with a builder prompt, publish to a repo). Same records, same stages,
same Actions: a build shows up in the Control Room like any other goal.

A playbook module defines PLAYBOOK = Playbook(...). Registered here by name, imported lazily,
so `work` does not depend on the packages that bring playbooks (the builder depends on `work`).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable

from .brain import Brain
from .model import Goal, GoalTree, Stage, Task

REGISTRY = {"build_agent": "forgeone.builder.playbook", "build_agent_package": "forgeone.builder.package_playbook"}


class StepFailed(Exception):
    """Raised by a step: the attempt failed for a known reason (goes into the task's failure taxonomy)."""

    def __init__(self, message: str, failure_reason: str = "tool_error"):
        super().__init__(message)
        self.failure_reason = failure_reason


@dataclass
class StepContext:
    goal: Goal
    tree: GoalTree
    task: Task
    brain: Brain
    deps: list[Task]
    run_id: str | None

    @property
    def args(self) -> dict:
        return self.goal.playbook_args

    @property
    def feedback(self) -> str:
        return f"\n\n# Feedback on your previous attempt\n{self.task.feedback}\n" if self.task.feedback else ""

    def task_by_step(self, step: str) -> Task | None:
        return next((t for t in self.tree.tasks if t.step == step and t.status != "superseded"), None)


@dataclass
class TaskTemplate:
    key: str
    title: str
    description: str
    type: str
    criteria: list[str]
    expected_output: str
    stages: list[Stage]
    step: str | None = None                 # None: one model call with the generic execute prompt
    verifier: str = "model"
    risk: str = "low"
    reversible: bool = True
    depends_on: list[str] = field(default_factory=list)   # keys of earlier tasks
    input: str = ""
    difficulty: int = 2
    model: str | None = None
    max_attempts: int = 3
    est_steps: int = 1
    est_minutes: float = 1.0


@dataclass
class MilestoneTemplate:
    title: str
    description: str
    done_when: list[str]
    tasks: list[TaskTemplate]
    approval: str | None = None             # human | ai | none; None: the goal's autonomy decides


@dataclass
class Playbook:
    name: str
    plan: Callable[[Goal], list[MilestoneTemplate]]
    steps: dict[str, Callable[[StepContext], Any]]
    session_name: str | None = None          # run name for its work sessions (builds keep "forgeone.build")
    session_fields: Callable[[Goal], dict] | None = None
    on_done: dict[str, Callable[[StepContext], None]] = field(default_factory=dict)   # after a step task is accepted


_REGISTERED: dict[str, Playbook] = {}


def register(playbook: Playbook) -> Playbook:
    """Make a playbook known by name at run time: agents built on the framework register theirs
    (e.g. "voyagerone.work") when their package is imported."""
    _REGISTERED[playbook.name] = playbook
    return playbook


def load(name: str) -> Playbook:
    if name in _REGISTERED:
        return _REGISTERED[name]
    if name in REGISTRY:
        return importlib.import_module(REGISTRY[name]).PLAYBOOK
    raise KeyError(f"unknown playbook {name!r} (known: {', '.join([*REGISTRY, *_REGISTERED])}). "
                   "An agent's playbook is known once its package is imported (run the agent's own command).")
