"""`forgeone build`: building an agent is a goal like any other.

1. The agent repository must be a clean, up-to-date main: checked before any tokens are spent.
2. A Goal with playbook "build_agent" is created (design -> spec -> publish, see playbook.py).
3. The work engine runs it in one session named "forgeone.build", so the Builds pages, spec
   versions and decisions keep working, and the goal shows up under Goals with every attempt.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings
from ..observability import start_run
from ..workspace import AgentRepo
from ..workspace import git as g
from .playbook import goal_title, session_fields


def _store():
    from ..work import MemoryStore, PgStore

    if settings().database_url:
        return PgStore()
    print("[forgeone] warning: DATABASE_URL is not set: this build runs, but its goal is not saved")
    return MemoryStore()


def run_build(agent: str = "voyagerone", goal_file: str | Path | None = None, publish: str = "branch",
              budget_usd: float | None = None, store=None, goal_id: str | None = None, brain=None, repo: AgentRepo | None = None) -> dict:
    """Returns {"run_id", "goal_id", "status", "tree", "spec_path", "published"}."""
    from ..work import Engine, Goal

    if repo is None and (goal_file is None or publish != "none"):
        repo = AgentRepo.for_agent(agent)
    if repo is not None:
        for w in repo.check_ready(pull=True):        # fail fast, before any tokens are spent
            print(f"[forgeone] warning: {w}")
    goal_path = Path(goal_file) if goal_file else repo.goal_path
    text = goal_path.read_text(encoding="utf-8")
    model = settings().builder_model
    name = goal_title(text, agent).rstrip(".")
    goal = Goal(
        **({"id": goal_id} if goal_id else {}),
        title=name if name.lower().startswith("build") else f"Build {agent}: {name}", description=text, agent_type="builder",
        autonomy="autonomous", playbook="build_agent", budget_usd=budget_usd,
        success_criteria=[f"agent/spec.yaml for {agent} passes its checks, review and edge tests",
                          "It is published for review, or identical to the current version"],
        constraints=["Tools only from the allowlist", "ForgeOne writes only inside agent/ of the agent repository"],
        playbook_args={"agent": agent, "publish": publish, "model": model, "goal_file": str(goal_path),
                       "repo_path": str(repo.path) if repo else None,
                       "goal_commit": g.git(repo.path, "rev-parse", "--short", "HEAD") if repo else None})
    engine = Engine(store=store or _store(), brain=brain)
    with start_run("forgeone.build", goal_id=goal.id, title=goal.title, **session_fields(goal)) as run_id:
        engine.create(goal)                          # one session for planning and working
        tree = engine.run(goal.id)
    spec_path = settings().runs_dir / run_id / "spec.yaml"
    pub = next((t for t in tree.tasks if t.step == "builder.publish"), None)
    return {"run_id": run_id, "goal_id": goal.id, "status": tree.goal.status, "tree": tree,
            "spec_path": spec_path if spec_path.exists() else None,
            "published": pub.output if pub and pub.status == "done" else None}
