"""How a task's execute stage is done.

- LLMExecutor: one model call writes the deliverable (write, analyze, decide, ...).
- AgentExecutor: the goal's agent (e.g. VoyagerOne) runs the task with its browser tools.
  The evals' programmatic checks run on its trace; the verify stage fails on a failed hard
  check without asking a model (cited a page it never opened, quoted text not on the page).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .brain import Answer, Brain, render
from .model import Task

CHECKS = ["cited_urls_were_opened", "quotes_found_on_pages", "no_run_error"]
CHECK_FAILURE = {"cited_urls_were_opened": "hallucination", "quotes_found_on_pages": "hallucination",
                 "no_run_error": "tool_error"}


@dataclass
class ExecResult:
    ok: bool
    output: str
    model: str | None
    prompt_version: str | None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    error: str | None = None
    failure_reason: str | None = None
    evidence: dict = field(default_factory=dict)   # what the verifier may use besides the output
    context: dict = field(default_factory=dict)    # manifest additions: a step's own manifest, an agent's history


class LLMExecutor:
    name = "llm"

    def __init__(self, brain: Brain):
        self.brain = brain

    def run(self, task: Task, context: dict) -> ExecResult:
        try:
            a: Answer = self.brain.write("execute", task.model, **context)
        except Exception as e:  # noqa: BLE001
            return ExecResult(False, "", task.model, None, error=f"{type(e).__name__}: {e}", failure_reason="model_error")
        if not a.value:
            return ExecResult(False, "", a.model, a.prompt_version, a.cost_usd, a.tokens_in, a.tokens_out,
                              a.duration_ms, error="empty output", failure_reason="criteria_not_met")
        return ExecResult(True, a.value, a.model, a.prompt_version, a.cost_usd, a.tokens_in, a.tokens_out, a.duration_ms)


class AgentExecutor:
    name = "agent"

    def __init__(self, agent: str, spec_path=None, browser_factory: Callable | None = None,
                 llm_factory: Callable | None = None):
        from ..evals.runner import load_spec

        self.agent = agent
        self.spec, self.spec_version = load_spec(agent, spec_path)
        self.browser_factory = browser_factory
        self.llm_factory = llm_factory
        self._browser = None
        self.policy = None                       # the goal's ContextPolicy, set by the engine

    @property
    def tools(self) -> list[str]:
        return list(self.spec.tools)

    def browser(self):
        if self._browser is None:
            if self.browser_factory:
                self._browser = self.browser_factory()
            else:
                from ..evals.runner import default_browser

                self._browser = default_browser()
        return self._browser

    def close(self):
        if self._browser is not None:
            self._browser.close()
            self._browser = None

    def run(self, task: Task, context: dict) -> ExecResult:
        from ..config import settings
        from ..evals.checks import run_checks
        from ..evals.suite import EvalCase
        from ..observability import LLMCallLogger
        from ..observability.context import run_dir_var
        from ..runtime import RunConfig, run_agent

        from .brain import prompt_version

        prompt = render("execute_agent", **context)
        from ..context import resolve

        model = task.model or settings().agent_model
        cfg = RunConfig(model=model, variant=f"task:{task.type}",
                        max_steps=min(self.spec.max_steps, max(5, 3 * (task.est_steps or 5))),
                        max_cost_usd=task.budget_usd, history=resolve(self.policy).history(model))
        t0 = time.perf_counter()
        try:
            trace = run_agent(self.spec, prompt, cfg, browser=self.browser(), case_id=task.code, agent=self.agent,
                              reports_dir=run_dir_var.get(),
                              llm=self.llm_factory(cfg.model) if self.llm_factory else None,
                              callbacks=[LLMCallLogger()])
        except Exception as e:  # noqa: BLE001
            return ExecResult(False, "", cfg.model, prompt_version("execute_agent"),
                              duration_ms=round((time.perf_counter() - t0) * 1000),
                              error=f"{type(e).__name__}: {e}", failure_reason="tool_error")
        output = trace.report or trace.final_answer or ""
        checks = run_checks(trace, EvalCase(id=task.code, category="factual", task=prompt, checks=CHECKS))
        evidence = {
            "agent": self.agent, "agent_version": self.spec_version, "stop_reason": trace.stop_reason,
            "opened_urls": sorted({c.args.get("url", "") for c in trace.tool_calls if c.name == "open_page" and c.ok}),
            "tool_calls": len(trace.tool_calls), "tool_errors": trace.tool_errors,
            "recoveries": list(trace.recoveries),
            "checks": [{"name": c.name, "passed": c.passed, "hard": c.hard, "detail": c.detail} for c in checks],
        }
        res = ExecResult(True, output, cfg.model, prompt_version("execute_agent"), trace.cost_usd, trace.tokens_in,
                         trace.tokens_out, trace.duration_ms, evidence=evidence, context={"agent": trace.context})
        if trace.stop_reason == "cost_limit":
            res.ok, res.error, res.failure_reason = False, "cost budget of the attempt used up", "budget_exceeded"
        elif trace.stop_reason == "error":
            res.ok, res.error, res.failure_reason = False, trace.error or "agent error", "tool_error"
        elif not output:
            res.ok, res.error, res.failure_reason = False, "the agent delivered nothing", "criteria_not_met"
        return res          # failed hard checks are judged in the verify stage, from evidence["checks"]
