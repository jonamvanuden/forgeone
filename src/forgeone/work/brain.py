"""All model calls of the work engine go through here: one place for prompts, models,
token usage and cost, so every Action records exactly what it cost and which prompt
version produced it."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from ..llm.pricing import call_cost, usage_of

PROMPTS = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def template(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").rstrip("\n")


def prompt_version(name: str) -> str:
    return f"{name}@{hashlib.sha256(template(name).encode()).hexdigest()[:8]}"


def render(name: str, **values: Any) -> str:
    return template(name).format(**values)


@dataclass
class Answer:
    value: Any                 # parsed object (ask) or text (write)
    model: str
    prompt_version: str
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0


def _cost(model: str, msg) -> tuple[float, int, int]:
    tin, tout, cread, cwrite = usage_of(msg)
    return call_cost(model, tin, tout, cread, cwrite) or 0.0, tin, tout


class Brain:
    """llm_factory(model_name) -> LangChain chat model. Tests pass a scripted fake."""

    def __init__(self, llm_factory: Callable[[str], Any] | None = None, planner_model: str | None = None,
                 judge_model: str | None = None, callbacks: list | None = None):
        if llm_factory is None:
            from ..llm import chat_model

            llm_factory = lambda m: chat_model(m)  # noqa: E731
        self.llm_factory = llm_factory
        from .policy import judge_model as routed_judge, planner_model as routed_planner

        self.planner_model = planner_model or routed_planner()
        self.judge_model = judge_model or routed_judge()
        self.callbacks = callbacks

    def _config(self) -> dict:
        if self.callbacks is not None:
            return {"callbacks": self.callbacks}
        from ..observability import LLMCallLogger

        return {"callbacks": [LLMCallLogger()]}

    def ask(self, schema, prompt_name: str, model: str, **values) -> Answer:
        """Structured answer from a prompt in work/prompts: returns a validated `schema` instance."""
        return self.structured(schema, render(prompt_name, **values), model, prompt_version(prompt_name))

    def write(self, prompt_name: str, model: str, **values) -> Answer:
        """Free text answer from a prompt in work/prompts."""
        return self.complete(render(prompt_name, **values), model, prompt_version(prompt_name))

    def structured(self, schema, prompt: str, model: str, version: str) -> Answer:
        """Structured answer for an already rendered prompt (playbook steps bring their own prompts)."""
        llm = self.llm_factory(model).with_structured_output(schema, method="json_schema", include_raw=True)
        t0 = time.perf_counter()
        out = llm.invoke(prompt, config=self._config())
        ms = round((time.perf_counter() - t0) * 1000)
        parsed = out.get("parsed") if isinstance(out, dict) else out
        if parsed is None:
            raise ValueError(f"{version}: model output did not match {schema.__name__}: {out.get('parsing_error')}")
        cost, tin, tout = _cost(model, out.get("raw") if isinstance(out, dict) else None)
        return Answer(parsed, model, version, cost, tin, tout, ms)

    def complete(self, prompt: str, model: str, version: str) -> Answer:
        """Free text answer for an already rendered prompt."""
        t0 = time.perf_counter()
        msg = self.llm_factory(model).invoke(prompt, config=self._config())
        ms = round((time.perf_counter() - t0) * 1000)
        content = msg.content if isinstance(msg.content, str) else "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in msg.content)
        cost, tin, tout = _cost(model, msg)
        return Answer(content.strip(), model, version, cost, tin, tout, ms)


def text_version(name: str, text: str) -> str:
    """Prompt version for prompts that live outside work/prompts (e.g. the builder's)."""
    return f"{name}@{hashlib.sha256(text.encode()).hexdigest()[:8]}"
