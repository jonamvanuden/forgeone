"""Agent: a blueprint turned into a playbook that ForgeOne's work engine runs.

    from forgeone.agentkit import Agent
    AGENT = Agent.from_file(Path(__file__).parent / "blueprint.yaml")

A command becomes a Goal whose plan is the blueprint's milestones and tasks, so a generated
agent works exactly like ForgeOne: goal -> milestones -> tasks -> actions, verified and logged.
"""
from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Callable

from ..work.model import Goal
from ..work.playbooks import MilestoneTemplate, Playbook, TaskTemplate, register
from . import steps
from .blueprint import AgentBlueprint, blueprint_checks

EXPECTED = {"think": "the deliverable in markdown",
            "browse": "findings with an exact quote and the URL of every page they come from",
            "report": "the final markdown report, ending with a Sources list"}


class Agent:
    def __init__(self, blueprint: AgentBlueprint, *, browser_factory: Callable | None = None,
                 llm_factory: Callable | None = None, callbacks: list | None = None):
        """browser_factory / llm_factory / callbacks: tests pass fakes; None means the real ones."""
        self.blueprint = blueprint
        self.name = blueprint.name
        self.version = blueprint.version()
        self.browser_factory = browser_factory
        self.llm_factory = llm_factory
        self.callbacks = callbacks
        self._browser = None
        self.playbook = Playbook(
            name=f"{self.name}.work", plan=self.plan,
            steps={f"agentkit.{k}": partial(fn, self) for k, fn in
                   (("think", steps.think), ("browse", steps.browse), ("report", steps.report))},
            session_name=f"{self.name}.run",
            session_fields=lambda g: {"agent": self.name, "agent_version": self.version})
        register(self.playbook)

    @classmethod
    def from_file(cls, path: str | Path, **kw) -> "Agent":
        return cls(AgentBlueprint.from_yaml(Path(path).read_text(encoding="utf-8")), **kw)

    # ---- the plan of every command ---------------------------------------------
    def plan(self, goal: Goal) -> list[MilestoneTemplate]:
        from ..config import settings

        model = goal.playbook_args.get("model") or settings().agent_model
        bp = self.blueprint
        return [MilestoneTemplate(
            title=m.title, description=m.description, done_when=list(m.done_when), approval="none",
            tasks=[TaskTemplate(
                key=t.key, step=f"agentkit.{t.kind}", title=t.title, description=t.instructions, type=t.type,
                criteria=list(t.criteria), stages=list(t.stages), depends_on=list(t.depends_on),
                difficulty=t.difficulty, model=model, input="the command and the output of the tasks it depends on",
                expected_output=EXPECTED[t.kind],
                est_steps=max(1, bp.max_steps // 2) if t.kind == "browse" else 1,
                est_minutes=2.0 if t.kind == "browse" else 0.5)
                for t in m.tasks])
            for m in bp.milestones]

    # ---- the browser, shared by the browse steps of one process -----------------
    def browser(self):
        if self._browser is None:
            if self.browser_factory:
                self._browser = self.browser_factory()
            else:
                from ..tools.browser import PlaywrightBrowser

                self._browser = PlaywrightBrowser()
        return self._browser

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            finally:
                self._browser = None


def check(agent: Agent) -> list[str]:
    """Problems that stop this agent from running; empty when it is fine. No model calls.
    Used by `<agent> check`, the package's tests and ForgeOne's build before publishing."""
    problems = [f"{c['name']}: {c['detail']}" for c in blueprint_checks(agent.blueprint, agent.name)
                if c["hard"] and not c["passed"]]
    try:
        plan = agent.plan(Goal(title="check", description="What is the capital of France?",
                               playbook=agent.playbook.name, playbook_args={"model": "claude-haiku-5-5"}))
    except Exception as e:  # noqa: BLE001
        return problems + [f"plan: {type(e).__name__}: {e}"]
    tasks = [t for m in plan for t in m.tasks]
    problems += [f"step {t.step} of task {t.key} is not a framework step" for t in tasks if t.step not in agent.playbook.steps]
    if not tasks:
        problems.append("plan: the blueprint has no tasks")
    return problems
