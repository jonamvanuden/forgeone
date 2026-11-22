"""The work engine end to end with a scripted model: planning, stages, retries with feedback,
human approvals, blocked dependencies, budgets, replanning. No API key, no database."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from forgeone.observability import start_run
from forgeone.work import Brain, Engine, Goal, MemoryStore, decide
from forgeone.work.approvals import NotWaiting
from forgeone.work.executors import ExecResult
from forgeone.work.policy import actual_difficulty, stages_for
from forgeone.work.schemas import (Approval, CriterionResult, EdgeCase, EdgeReport, MilestoneDraft, MilestonePlan,
                                   TaskDraft, TaskPlan, Verdict)

USAGE = {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100}


def draft(title, type="write", difficulty=2, risk="low", deps=(), reversible=True):
    return TaskDraft(title=title, description=f"do {title}", type=type, difficulty=difficulty, risk=risk,
                     reversible=reversible, acceptance_criteria=[f"{title} is complete", f"{title} is correct"],
                     depends_on=list(deps), input="", expected_output=f"the {title}", est_steps=2, est_minutes=1)


class Script:
    """Decides what the fake model answers. Override per test."""

    def __init__(self):
        self.milestones = [MilestoneDraft(title="Facts gathered", description="d", done_when=["facts listed"]),
                           MilestoneDraft(title="Report written", description="d", done_when=["report exists"])]
        self.tasks = {"Facts gathered": [draft("facts list"), draft("summary", deps=[1], risk="medium")],
                      "Report written": [draft("report", difficulty=4)]}
        self.subtasks = [draft("part one"), draft("part two", deps=[1])]
        self.verify_fail = {}        # task title -> number of verify failures before passing
        self.approve_ok = True
        self.milestone_ok = True
        self.prompts = []

    def structured(self, schema, prompt):
        self.prompts.append((schema.__name__, prompt))
        if schema is MilestonePlan:
            return MilestonePlan(reasoning="two steps", milestones=self.milestones)
        if schema is TaskPlan:
            if "too big for one run. Split" in prompt:
                return TaskPlan(tasks=self.subtasks)
            title = prompt.split("# Milestone to break down: ")[1].split("\n")[0].split(" ", 1)[1]
            return TaskPlan(tasks=self.tasks[title])
        if schema is Verdict:
            title = prompt.split("# Task: ")[1].split("\n")[0]
            left = self.verify_fail.get(title, 0)
            if left:
                self.verify_fail[title] = left - 1
                return Verdict(criteria=[CriterionResult(criterion="c", met=False, note="missing the numbers")],
                               passed=False, confidence=0.9, reason="numbers missing",
                               failure_reason="criteria_not_met", feedback="add the numbers")
            return Verdict(criteria=[CriterionResult(criterion="c", met=True, note="ok")], passed=True,
                           confidence=0.8, reason="all met", feedback="")
        if schema is EdgeReport:
            return EdgeReport(cases=[EdgeCase(case="conflicting sources", handled=True, note="ok")], passed=True, feedback="")
        if schema is Approval:
            ok = self.milestone_ok if "Decide whether a milestone" in prompt else self.approve_ok
            return Approval(approved=ok, confidence=0.7, reason="fine" if ok else "not good enough",
                            feedback="" if ok else "be more specific")
        raise AssertionError(schema)

    def text(self, prompt):
        self.prompts.append(("execute", prompt))
        return "the deliverable"


class FakeChat:
    def __init__(self, script):
        self.script = script

    def with_structured_output(self, schema, **kw):
        s = self.script

        class Bound:
            def invoke(self, prompt, config=None):
                return {"raw": AIMessage("", usage_metadata=USAGE), "parsed": s.structured(schema, prompt),
                        "parsing_error": None}
        return Bound()

    def invoke(self, prompt, config=None):
        return AIMessage(self.script.text(prompt), usage_metadata=USAGE)


@pytest.fixture
def env(tmp_path):
    script = Script()
    store = MemoryStore()
    brain = Brain(llm_factory=lambda m: FakeChat(script), planner_model="claude-haiku-5-5",
                  judge_model="claude-haiku-5-5", callbacks=[])
    with start_run("test", runs_root=tmp_path):
        yield script, store, Engine(store=store, brain=brain)


def new_goal(engine, **kw):
    g = Goal(title="Find out about sugar", success_criteria=["a report exists"], **kw)
    return engine.create(g)


def test_plan_builds_the_tree(env):
    script, store, engine = env
    script.tasks["Facts gathered"].append(draft("big one", difficulty=5, deps=[1]))
    tree = new_goal(engine)
    ms = tree.active_milestones()
    assert [m.seq for m in ms] == [1, 2] and tree.goal.plan_version == 1 and tree.goal.status == "planned"
    t = {x.code: x for x in tree.tasks}
    assert set(t) == {"1.1", "1.2", "1.3", "1.3.1", "1.3.2", "2.1"}
    assert t["1.2"].depends_on == [t["1.1"].id]
    assert t["1.3"].stages == [] and t["1.3"].executor == "none"              # split into subtasks
    assert t["1.3.1"].depends_on == [t["1.1"].id] and t["1.3.2"].depends_on == [t["1.1"].id, t["1.3.1"].id]
    assert t["1.3.1"].depth == 2 and t["1.3.1"].difficulty <= 4
    assert t["1.1"].stages == ["execute", "verify"] and t["1.2"].approver == "ai"
    assert t["2.1"].model == "claude-sonnet-5-5" and "difficulty 4" in t["2.1"].routing_reason
    assert t["1.1"].est_cost_usd and t["1.1"].budget_usd >= 0.05
    plans = [a for a in tree.actions if a.kind == "plan"]
    assert len(plans) == 4 and all(a.status == "passed" and a.cost_usd > 0 and a.prompt_version for a in plans)
    assert tree.goal.cost_usd == pytest.approx(sum(a.cost_usd for a in tree.actions))


def test_supervised_goal_waits_for_milestone_approval(env):
    script, store, engine = env
    gid = new_goal(engine).goal.id
    tree = engine.run(gid)
    assert tree.goal.status == "waiting" and "M1" in tree.goal.note
    m1 = tree.active_milestones()[0]
    assert m1.status == "waiting"
    assert [t.status for t in tree.tasks_of(m1.id)] == ["done", "done"]
    kinds = [a.kind for a in tree.actions_of(tree.tasks_of(m1.id)[1].id)]
    assert kinds == ["execute", "verify", "edge_test", "approve"]             # medium risk: all four stages
    with pytest.raises(NotWaiting):
        decide(store, tree.tasks_of(m1.id)[0].id, True)
    assert "approved" in decide(store, m1.id, True, "looks right")
    assert store.load(gid).goal.status == "paused"
    tree = engine.run(gid)
    assert tree.goal.status == "waiting" and "M2" in tree.goal.note
    decide(store, gid, True)                                                  # the goal id works when one thing waits
    tree = engine.run(gid)
    assert tree.goal.status == "done" and tree.goal.finished_at
    done = tree.leaf_tasks()
    assert all(t.status == "done" and t.cost_usd > 0 and t.actual_difficulty for t in done)
    assert all(t.actual_difficulty == t.difficulty - 1 for t in done)        # first try, within estimate


def test_verify_failure_feeds_the_next_attempt(env):
    script, store, engine = env
    script.verify_fail["facts list"] = 1
    gid = new_goal(engine, autonomy="autonomous").goal.id
    tree = engine.run(gid)
    t = next(x for x in tree.tasks if x.title == "facts list")
    assert t.status == "done" and t.attempts == 2
    acts = tree.actions_of(t.id)
    assert [(a.kind, a.attempt, a.status) for a in acts] == [
        ("execute", 1, "passed"), ("verify", 1, "failed"), ("execute", 2, "passed"), ("verify", 2, "passed")]
    assert acts[1].failure_reason == "criteria_not_met" and acts[1].criteria[0]["met"] is False
    second_execute = [p for k, p in script.prompts if k == "execute" and "do facts list" in p][1]
    assert "add the numbers" in second_execute
    assert t.actual_difficulty == t.difficulty + 1.5 + 1
    assert tree.goal.status == "done"                                         # autonomous: AI approves milestones


def test_failed_task_blocks_dependents_and_fails_goal(env):
    script, store, engine = env
    script.verify_fail["facts list"] = 99
    tree = engine.run(new_goal(engine).goal.id)
    facts, summary = tree.tasks_of(tree.active_milestones()[0].id)
    assert facts.status == "failed" and facts.attempts == 3 and facts.actual_difficulty == 5
    assert summary.status == "blocked" and summary.failure_reason == "blocked_dependency"
    assert tree.active_milestones()[0].status == "failed" and tree.goal.status == "failed"
    assert tree.active_milestones()[1].status == "pending"


def test_high_risk_task_waits_for_your_sign_off_and_reject_reruns_with_note(env):
    script, store, engine = env
    script.tasks["Facts gathered"] = [draft("press release", risk="high")]
    gid = new_goal(engine).goal.id
    tree = engine.run(gid)
    t = tree.tasks_of(tree.active_milestones()[0].id)[0]
    assert t.stages == ["execute", "verify", "edge_test", "approve"] and t.approver == "human"
    assert t.status == "waiting" and tree.goal.status == "waiting"
    with pytest.raises(ValueError):
        decide(store, t.id, False)                                            # a rejection needs a note
    decide(store, t.id, False, "use the 2015 guideline")
    t = store.load(gid).task(t.id)
    assert t.status == "pending" and t.feedback == "use the 2015 guideline"
    tree = engine.run(gid)
    assert "use the 2015 guideline" in [p for k, p in script.prompts if k == "execute"][-1]
    decide(store, t.id.lower(), True)                                         # ids are case-insensitive
    t = store.load(gid).task(t.id)
    assert t.status == "done" and t.attempts == 2


def test_irreversible_task_waits_for_a_go_ahead_before_it_runs(env):
    script, store, engine = env
    script.tasks["Facts gathered"] = [draft("draft the mail"), draft("send the mail", risk="high", reversible=False, deps=[1])]
    gid = new_goal(engine).goal.id
    tree = engine.run(gid)
    draft_t, send = tree.tasks_of(tree.active_milestones()[0].id)
    assert send.stages == ["approve", "execute", "verify"] and send.status == "waiting"
    assert draft_t.status == "done" and not [a for a in tree.actions_of(send.id) if a.kind == "execute"]
    from forgeone.work.views import stage_states, waiting_on_you
    assert [s["state"] for s in stage_states(tree, send)] == ["pending", "none", "none"]
    assert waiting_on_you(tree)[0]["kind"] == "gate"
    assert "go-ahead" in decide(store, send.id, True)
    tree = engine.run(gid)
    send = tree.task(send.id)
    assert [a.kind for a in tree.actions_of(send.id)] == ["approve", "execute", "verify"] and send.status == "done"
    assert [s["state"] for s in stage_states(tree, send)] == ["passed", "passed", "passed"]


def test_budget_pauses_the_goal(env):
    script, store, engine = env
    gid = new_goal(engine, budget_usd=0.0007).goal.id                         # planning alone costs 0.00045
    tree = engine.run(gid)
    assert tree.goal.status == "paused" and "budget" in tree.goal.note
    assert not [t for t in tree.tasks if t.status == "running"]


def test_max_tasks_stops_politely(env):
    script, store, engine = env
    gid = new_goal(engine, autonomy="autonomous").goal.id
    tree = engine.run(gid, max_tasks=1)
    assert tree.goal.status == "paused" and "--max-tasks" in tree.goal.note
    assert sum(t.status == "done" for t in tree.tasks) == 1
    assert engine.run(gid).goal.status == "done"


def test_autonomous_goal_replans_after_a_failed_milestone(env):
    script, store, engine = env
    script.milestone_ok = False
    tree = engine.run(new_goal(engine, autonomy="autonomous").goal.id)
    assert tree.goal.plan_version == 3 and tree.goal.status == "failed"       # 2 automatic replans, then stop
    assert {m.status for m in tree.milestones if m.plan_version < 3} <= {"superseded", "failed"}
    assert all(t.status == "superseded" for t in tree.tasks if t.plan_version == 1)
    replan = [a for a in tree.actions if a.kind == "plan" and a.input.get("plan_version") == 2][0]
    assert "M1 Facts gathered failed" in replan.input["reason"]


def test_hard_check_failure_needs_no_model_call(env):
    script, store, engine = env
    script.tasks["Facts gathered"] = [draft("sources", type="research")]

    class FakeAgent:
        name, tools = "agent", ["web_search", "open_page", "write_report"]
        runs = 0

        def run(self, task, context):
            FakeAgent.runs += 1
            ok = FakeAgent.runs > 1
            return ExecResult(True, "report", "claude-haiku-5-5", "execute_agent@x", 0.001,
                              evidence={"opened_urls": ["https://a.org"], "checks": [
                                  {"name": "cited_urls_were_opened", "passed": ok, "hard": True, "detail": "1 not opened"}]})

        def close(self):
            pass

    engine.agent_factory = lambda goal: FakeAgent()
    tree = engine.create(Goal(title="g", agent="voyagerone", autonomy="autonomous"))
    t = tree.tasks_of(tree.active_milestones()[0].id)[0]
    assert t.executor == "agent"
    tree = engine.run(tree.goal.id)
    t = tree.task(t.id)
    v1 = [a for a in tree.actions_of(t.id) if a.kind == "verify"][0]
    assert v1.actor == "checks" and v1.actor_type == "tool" and v1.failure_reason == "hallucination" and v1.cost_usd == 0
    assert t.status == "done" and t.attempts == 2


def test_policy_rules():
    assert stages_for("low") == ["execute", "verify"]
    assert stages_for("high")[-1] == "approve"
    assert stages_for("low", reversible=False) == ["approve", "execute", "verify"]
    from forgeone.work.model import Task
    t = Task(goal_id="g", milestone_id="m", plan_version=1, seq=1, code="1.1", title="x", difficulty=3,
             attempts=1, est_cost_usd=0.01, cost_usd=0.04, status="done")
    assert actual_difficulty(t, 0) == 5.0                                     # 3 + capped overrun of 2
    t.cost_usd, t.attempts = 0.005, 1
    assert actual_difficulty(t, 0) == 2.0
    t.status = "failed"
    assert actual_difficulty(t, 0) == 5.0
