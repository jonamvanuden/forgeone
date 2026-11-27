"""The three framework steps every generated agent is made of: think, browse, report.

They are ordinary playbook steps of the work engine, so every attempt is an Action with its
cost, context manifest and verdict, and the engine's verify stage judges them: hard checks from
the evidence first (no model call when one fails), then a reviewer model against the criteria.
"""
from __future__ import annotations

import re
import time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ..context import ContextPack
from ..work.brain import text_version
from ..work.executors import ExecResult
from ..work.playbooks import StepContext, StepFailed

if TYPE_CHECKING:
    from .agent import Agent

PROMPTS = Path(__file__).parent / "prompts"
BROWSE_CHECKS = ["cited_urls_were_opened", "quotes_found_on_pages", "no_run_error"]
OPENED_HEADING = "## Pages opened in this step"
URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+")


@lru_cache(maxsize=None)
def template(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").rstrip("\n")


def version(name: str) -> str:
    return text_version(f"agentkit.{name}", template(name))


def clean_url(u: str) -> str:
    return u.rstrip(".,;:!?*_").rstrip("/")


def urls_in(text: str) -> list[str]:
    return [clean_url(u) for u in URL.findall(text or "")]


def opened_pages(text: str) -> list[str]:
    """The URLs a browse step recorded as opened (the section it appends to its output)."""
    if OPENED_HEADING not in (text or ""):
        return []
    return urls_in(text.split(OPENED_HEADING, 1)[1])


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {c}" for i, c in enumerate(items, 1)) or "none"


def _pack(agent: "Agent", ctx: StepContext, name: str) -> ContextPack:
    pack = ContextPack(f"{agent.name}.{name}", ctx.task.model)
    pack.add("command", ctx.goal.description, name="the command", source=ctx.goal.id, required=True)
    pack.add("task", ctx.task.title, name="task", required=True)
    pack.add("instructions", ctx.task.description, name="instructions", source=f"blueprint {agent.version}", required=True)
    pack.add("criteria", _numbered(ctx.task.acceptance_criteria), name="acceptance criteria", required=True)
    pack.join("inputs", [(f"## {d.code} {d.title}", d.id, d.output or "") for d in ctx.deps], name="earlier tasks",
              empty="none: this is the first task")
    pack.add("feedback", ctx.feedback, name="feedback on the previous attempt", required=True)
    return pack


def _call(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        raise StepFailed(f"{type(e).__name__}: {e}", "model_error") from e


def _model(agent: "Agent", ctx: StepContext) -> str:
    from ..config import settings

    return ctx.task.model or ctx.args.get("model") or settings().agent_model


# ------------------------------------------------------------------ think
def think(agent: "Agent", ctx: StepContext) -> ExecResult:
    pack = _pack(agent, ctx, "think")
    v = pack.values
    prompt = template("think").format(agent=agent.name, command=v["command"], task=v["task"],
                                      instructions=v["instructions"], criteria=v["criteria"], inputs=v["inputs"]) + v["feedback"]
    ans = _call(lambda: ctx.brain.complete(prompt, _model(agent, ctx), version("think")))
    if not ans.value:
        raise StepFailed("the model returned nothing", "criteria_not_met")
    return ExecResult(True, ans.value, ans.model, ans.prompt_version, ans.cost_usd, ans.tokens_in, ans.tokens_out,
                      ans.duration_ms, context=pack.manifest(template("think"), version("think")))


# ------------------------------------------------------------------ browse
def browse(agent: "Agent", ctx: StepContext) -> ExecResult:
    from ..evals.checks import run_checks
    from ..evals.suite import EvalCase
    from ..observability import LLMCallLogger
    from ..observability.context import run_dir_var
    from ..runtime import RunConfig, run_agent

    pack = _pack(agent, ctx, "browse")
    v = pack.values
    prompt = template("browse").format(command=v["command"], task=v["task"], instructions=v["instructions"],
                                       criteria=v["criteria"], inputs=v["inputs"]) + v["feedback"]
    model = _model(agent, ctx)
    spec = agent.blueprint.spec()
    cfg = RunConfig(model=model, variant=f"{agent.name}:{ctx.task.code}", max_steps=spec.max_steps,
                    max_cost_usd=ctx.task.budget_usd)
    t0 = time.perf_counter()
    try:
        trace = run_agent(spec, prompt, cfg, browser=agent.browser(), case_id=ctx.task.code, agent=agent.name,
                          reports_dir=run_dir_var.get(), llm=agent.llm_factory(model) if agent.llm_factory else None,
                          callbacks=agent.callbacks if agent.callbacks is not None else [LLMCallLogger()])
    except Exception as e:  # noqa: BLE001
        return ExecResult(False, "", model, version("browse"), duration_ms=round((time.perf_counter() - t0) * 1000),
                          error=f"{type(e).__name__}: {e}", failure_reason="tool_error")
    opened = sorted({clean_url(c.args.get("url", "")) for c in trace.tool_calls if c.name == "open_page" and c.ok}
                    - {""})
    findings = (trace.report or trace.final_answer or "").strip()
    output = (f"{findings}\n\n{OPENED_HEADING}\n" + ("\n".join(f"- {u}" for u in opened) or "none")) if findings else ""
    checks = [{"name": c.name, "passed": c.passed, "hard": c.hard, "detail": c.detail}
              for c in run_checks(trace, EvalCase(id=ctx.task.code, category="factual", task=prompt, checks=BROWSE_CHECKS))]
    checks.append({"name": "read_at_least_one_page", "hard": True, "passed": bool(opened),
                   "detail": f"{len(opened)} pages opened",
                   "fix": "Open and read at least one relevant page before handing in findings.",
                   "failure_reason": "tool_error" if trace.tool_errors and not opened else "criteria_not_met"})
    evidence = {"agent": agent.name, "agent_version": agent.version, "stop_reason": trace.stop_reason,
                "opened_urls": opened, "tool_calls": len(trace.tool_calls), "tool_errors": trace.tool_errors,
                "recoveries": list(trace.recoveries), "checks": checks}
    res = ExecResult(True, output, model, version("browse"), trace.cost_usd or 0.0, trace.tokens_in, trace.tokens_out,
                     trace.duration_ms or 0, evidence=evidence,
                     context={**pack.manifest(template("browse"), version("browse")), "agent": trace.context})
    if trace.stop_reason == "cost_limit":
        res.ok, res.error, res.failure_reason = False, "cost budget of the attempt used up", "budget_exceeded"
    elif trace.stop_reason == "error":
        res.ok, res.error, res.failure_reason = False, trace.error or "agent error", "tool_error"
    elif not findings:
        res.ok, res.error, res.failure_reason = False, "no findings were handed in", "criteria_not_met"
    return res


# ------------------------------------------------------------------ report
def report(agent: "Agent", ctx: StepContext) -> ExecResult:
    from ..observability.context import run_dir_var

    pack = _pack(agent, ctx, "report")
    allowed = sorted({u for d in ctx.deps for u in opened_pages(d.output or "")})
    sources = pack.fixed("sources", "\n".join(f"- {u}" for u in allowed) or "none: no page was opened")
    v = pack.values
    prompt = template("report").format(agent=agent.name, command=v["command"], task=v["task"],
                                       instructions=v["instructions"], criteria=v["criteria"], inputs=v["inputs"],
                                       sources=sources) + v["feedback"]
    ans = _call(lambda: ctx.brain.complete(prompt, _model(agent, ctx), version("report")))
    text = (ans.value or "").strip()
    if not text:
        raise StepFailed("the model returned an empty report", "criteria_not_met")
    cited = sorted(set(urls_in(text)))
    unopened = [u for u in cited if u not in set(allowed)]
    checks = [
        {"name": "cites_only_opened_pages", "hard": True, "passed": not unopened,
         "detail": ", ".join(unopened) or f"all {len(cited)} cited URLs were opened",
         "fix": "Cite only URLs from the list of opened pages, exactly as written there.", "failure_reason": "hallucination"},
        {"name": "has_sources", "hard": bool(allowed), "passed": bool(cited) or not allowed,
         "detail": f"{len(cited)} sources cited, {len(allowed)} pages were opened",
         "fix": "End with a '## Sources' section that lists the opened pages you used."},
    ]
    path = None
    if run_dir_var.get() is not None:
        out = Path(run_dir_var.get()) / f"{agent.name}-report-{ctx.task.code}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        path = str(out)
    return ExecResult(True, text, ans.model, ans.prompt_version, ans.cost_usd, ans.tokens_in, ans.tokens_out,
                      ans.duration_ms, evidence={"checks": checks, "sources": cited, "opened_urls": allowed,
                                                 "report_path": path},
                      context=pack.manifest(template("report"), version("report")))
