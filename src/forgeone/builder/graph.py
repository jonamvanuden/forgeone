"""The builder graph.

    today:   START -> plan -> build -> END
    next:    ... build -> test -> judge --pass--> publish
                            ^                |
                            +----- repair <--+ fail, budget left
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from langgraph.graph import END, START, StateGraph

from ..config import settings
from ..observability import LLMCallLogger, log_decision, start_run
from ..workspace import AgentRepo
from ..workspace import git as g
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


def build_report(run_id: str, agent: str, model: str, spec: dict, plan: str, goal_commit: str | None) -> str:
    """agent/BUILD.md: how this version was built, committed next to the spec."""
    url = settings().control_room_url
    tests = "\n".join(f"- {t['task']}" for t in spec.get("test_cases", []))
    return f"""# Build {run_id}

| | |
|---|---|
| Agent | {agent} |
| Built | {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} |
| Model | {model} |
| Goal | `goal.md` at commit `{goal_commit or "unknown"}` |
| Trace | {f"[Control Room]({url}/#/run/{run_id})" if url else run_id} |

## Spec summary
- Tools: {", ".join(spec.get("tools", []))}
- Step budget: {spec.get("max_steps")}
- System prompt: {len(spec.get("system_prompt", ""))} characters

Builder test cases (for quick feedback only; promotion is decided by `evals/suite.yaml`):
{tests}

## Plan
{plan.strip()}
"""


def run_build(agent: str = "voyagerone", goal_file: str | Path | None = None, publish: str = "branch") -> dict:
    """Build an agent from the goal in its own repository and publish the result there.

    publish: "branch" (default) commits agent/ on branch forgeone/<run-id> and pushes it,
             "main" commits straight to the base branch, "none" leaves the repo untouched.
    Returns {"run_id", "spec", "spec_path", "publish"}.
    """
    repo = None
    if goal_file is None or publish != "none":
        repo = AgentRepo.for_agent(agent)
        for w in repo.check_ready(pull=True):     # fail fast, before any tokens are spent
            print(f"[forgeone] warning: {w}")
    goal_path = Path(goal_file) if goal_file else repo.goal_path
    goal = goal_path.read_text(encoding="utf-8")
    goal_commit = g.git(repo.path, "rev-parse", "--short", "HEAD") if repo else None
    model = settings().builder_model
    published = None
    with start_run("forgeone.build", goal=goal, goal_file=str(goal_path), agent=agent, model=model,
                   agent_repo=str(repo.path) if repo else None, goal_commit=goal_commit) as run_id:
        result = build_graph().invoke(
            {"goal": goal},
            config={"callbacks": [LLMCallLogger()], "run_name": "forgeone.build",
                    "metadata": {"run_id": run_id, "agent": agent}},
        )
        spec_yaml = yaml.safe_dump(result["spec"], sort_keys=False, allow_unicode=True)
        out = settings().runs_dir / run_id / "spec.yaml"
        out.write_text(spec_yaml)
        print(f"\nspec: {out}")

        if repo is not None and publish != "none":
            published = repo.publish(
                run_id,
                {"agent/spec.yaml": spec_yaml,
                 "agent/BUILD.md": build_report(run_id, agent, model, result["spec"], result.get("plan", ""), goal_commit)},
                message=f"forgeone: build {run_id}\n\nModel: {model}\nGoal commit: {goal_commit}",
                mode=publish,
            )
            if published.unchanged:
                log_decision("not_published", f"spec identical to {agent}'s current version", agent=agent)
                print(f"{agent}: spec identical to the current version, nothing to publish")
                return {"run_id": run_id, "spec": result["spec"], "spec_path": out, "publish": published}
            log_decision("published", f"spec committed to the {agent} repository ({published.mode})",
                         agent=agent, branch=published.branch, commit=published.commit,
                         pushed=published.pushed, url=published.url, push_error=published.push_error)
            where = f"branch {published.branch}" if published.mode == "branch" else published.branch
            print(f"published to {agent}: {where} @ {published.commit}"
                  + (", pushed" if published.pushed else f", NOT pushed ({published.push_error or 'no remote'})"))
            if published.url:
                print(f"review: {published.url}")
    return {"run_id": run_id, "spec": result["spec"], "spec_path": out, "publish": published}
