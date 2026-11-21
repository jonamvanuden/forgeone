"""The agent loop: model -> tool calls -> model ... until the agent answers or a budget runs out.

Deliberately a small explicit loop (not a prebuilt agent): the runtime itself enforces the
budgets, records every tool call, error, retry and recovery, and returns an AgentTrace that
evals can check. The agent can never raise its own limits.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from ..evals.trace import AgentTrace, ToolCall
from ..llm import chat_model
from ..llm.pricing import call_cost
from ..observability import log_event
from ..spec import AgentSpec
from ..tools import Browser, ToolContext, ToolResult, toolset

FINAL_REMINDER = ("\n\nWhen you are done, call write_report exactly once with the full report, "
                  "then reply with a short summary and no further tool calls.")


@dataclass
class RunConfig:
    model: str
    variant: str = "default"
    max_steps: int | None = None          # None -> spec.max_steps
    max_cost_usd: float | None = None
    tools: list[str] | None = None        # None -> spec.tools
    prompt_suffix: str = ""
    max_llm_retries: int = 2
    retry_backoff_s: float = 2.0


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type", "text") == "text")


def _invoke_with_retries(llm, messages, cfg: RunConfig, callbacks, trace: AgentTrace):
    for attempt in range(cfg.max_llm_retries + 1):
        try:
            return llm.invoke(messages, config={"callbacks": callbacks or []})
        except Exception as e:  # network, rate limit, overload: retry, then give up
            if attempt == cfg.max_llm_retries:
                raise
            trace.llm_retries += 1
            log_event("llm.retry", logging.WARNING, attempt=attempt + 1, error=f"{type(e).__name__}: {e}")
            time.sleep(cfg.retry_backoff_s * (2 ** attempt))


def run_agent(spec: AgentSpec, task: str, cfg: RunConfig, *, browser: Browser | None, case_id: str = "adhoc",
              agent: str | None = None, reports_dir: Path | None = None, llm=None, callbacks=None) -> AgentTrace:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    tools = toolset(cfg.tools or list(spec.tools))
    by_name = {t.name: t for t in tools}
    max_steps = cfg.max_steps or spec.max_steps
    ctx = ToolContext(browser=browser, reports_dir=reports_dir)
    trace = AgentTrace(case_id=case_id, task=task, agent=agent or spec.name, variant=cfg.variant,
                       model=cfg.model, steps_limit=max_steps)
    bound = (llm or chat_model(cfg.model)).bind_tools([t.schema() for t in tools])
    messages = [SystemMessage(spec.system_prompt + cfg.prompt_suffix + FINAL_REMINDER), HumanMessage(task)]
    t0, cost, last_failed = time.perf_counter(), 0.0, False
    log_event("agent.start", agent=trace.agent, variant=cfg.variant, model=cfg.model, case_id=case_id,
              max_steps=max_steps, max_cost_usd=cfg.max_cost_usd, tools=[t.name for t in tools])
    try:
        while True:
            if cfg.max_cost_usd is not None and cost >= cfg.max_cost_usd:
                trace.hit_cost_limit, trace.stop_reason = True, "cost_limit"
                break
            ai = _invoke_with_retries(bound, messages, cfg, callbacks, trace)
            trace.llm_calls += 1
            usage = getattr(ai, "usage_metadata", None) or {}
            tin, tout = usage.get("input_tokens", 0) or 0, usage.get("output_tokens", 0) or 0
            trace.tokens_in += tin
            trace.tokens_out += tout
            cost += call_cost(cfg.model, tin, tout) or 0.0
            messages.append(ai)
            calls = list(getattr(ai, "tool_calls", None) or [])
            if not calls:
                trace.final_answer, trace.stop_reason = _text(ai.content), "finished"
                break
            for tc in calls:
                if len(trace.tool_calls) >= max_steps:
                    # every tool_use needs a tool_result; then stop
                    messages.append(ToolMessage("Step budget exhausted.", tool_call_id=tc["id"]))
                    trace.hit_step_limit, trace.stop_reason = True, "step_limit"
                    continue
                name, args = tc["name"], tc.get("args") or {}
                s0 = time.perf_counter()
                if name not in by_name:
                    res = ToolResult(content=f"ERROR: tool '{name}' is not available to you.", ok=False,
                                     error="tool not allowed")
                else:
                    try:
                        res = by_name[name].fn(ctx, args)
                    except Exception as e:  # tool failures go back to the model; it may recover
                        res = ToolResult(content=f"ERROR: {type(e).__name__}: {e}", ok=False,
                                         error=f"{type(e).__name__}: {e}")
                ms = round((time.perf_counter() - s0) * 1000)
                call = ToolCall(name=name, args=args, ok=res.ok, error=res.error, result_preview=res.content[:300],
                                page_text=res.page_text, duration_ms=ms, after_error=last_failed)
                trace.tool_calls.append(call)
                log_event("tool.call" if res.ok else "tool.error", logging.INFO if res.ok else logging.WARNING,
                          tool=name, args={k: str(v)[:200] for k, v in args.items()}, ok=res.ok, error=res.error,
                          duration_ms=ms, step=len(trace.tool_calls), recovery_attempt=last_failed)
                last_failed = not res.ok
                messages.append(ToolMessage(res.content, tool_call_id=tc["id"]))
            if trace.hit_step_limit:
                break
    except Exception as e:
        trace.error, trace.stop_reason = f"{type(e).__name__}: {e}", "error"
        log_event("agent.error", logging.ERROR, error=trace.error)
    trace.report = ctx.report
    trace.cost_usd = round(cost, 6)
    trace.duration_ms = round((time.perf_counter() - t0) * 1000)
    attempts, successes = trace.recoveries
    log_event("agent.end", stop_reason=trace.stop_reason, tool_calls=len(trace.tool_calls),
              tool_errors=trace.tool_errors, recovery_attempts=attempts, recovery_successes=successes,
              llm_calls=trace.llm_calls, llm_retries=trace.llm_retries, cost_usd=trace.cost_usd,
              tokens_in=trace.tokens_in, tokens_out=trace.tokens_out,
              duration_ms=trace.duration_ms, has_report=trace.report is not None)
    return trace
