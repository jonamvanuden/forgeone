"""The builder graph.

    today:   START -> plan -> build -> END
    next:    ... build -> test -> judge --pass--> publish
                            ^                |
                            +----- repair <--+ fail, budget left
"""
from __future__ import annotations

from pathlib import Path

import yaml
from langgraph.graph import END, START, StateGraph

from ..config import settings
from ..observability import LLMCallLogger, start_run
from .nodes import build_node, plan_node
from .state import ForgeState


def build_graph():
    g = StateGraph(ForgeState)
    g.add_node("plan", plan_node)
    g.add_node("build", build_node)
    g.add_edge(START, "plan")
    g.add_edge("plan", "build")
    g.add_edge("build", END)
    return g.compile()


def goal_file_for(agent: str) -> Path:
    return settings().agent_dir(agent) / "goal.md"


def run_build(agent: str = "voyagerone", goal_file: str | Path | None = None) -> dict:
    """Build an agent from its goal. Returns {"run_id", "spec", "spec_path"}."""
    goal_path = Path(goal_file) if goal_file else goal_file_for(agent)
    goal = goal_path.read_text(encoding="utf-8")
    model = settings().builder_model
    with start_run("forgeone.build", goal=goal, goal_file=str(goal_path), agent=agent, model=model) as run_id:
        result = build_graph().invoke(
            {"goal": goal},
            config={"callbacks": [LLMCallLogger()], "run_name": "forgeone.build",
                    "metadata": {"run_id": run_id, "agent": agent}},
        )
        out = settings().runs_dir / run_id / "spec.yaml"
        out.write_text(yaml.safe_dump(result["spec"], sort_keys=False, allow_unicode=True))
        print(f"\nspec: {out}")
    return {"run_id": run_id, "spec": result["spec"], "spec_path": out}
