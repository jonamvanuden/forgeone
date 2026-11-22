"""Steps 3 to 5 of context management: token budgets, memory (attempt history and lessons),
context policies for goals and eval variants."""
from __future__ import annotations

import pytest
from test_context import LongBrowser, RecordingLLM
from test_runtime import SPEC
from test_work import Script, draft, env, new_goal  # noqa: F401  (env is a fixture)

from forgeone.context import PRESETS, ContextPack, HistoryPolicy, budget_for, resolve
from forgeone.context.pack import P_DEPS, P_LESSONS
from forgeone.evals.variants import Variant
from forgeone.memory import find_lessons
from forgeone.runtime import RunConfig, run_agent
from forgeone.work import decide
from forgeone.work.schemas import MilestoneDraft


# ---------------------------------------------------------------- step 3: budgets
def test_fit_shortens_then_leaves_out_lowest_priority_first():
    p = ContextPack("execute")
    p.add("criteria", "c" * 400, required=True)
    p.join("deps", [("## 1.1", "T-1", "d" * 8000), ("## 1.2", "T-2", "e" * 8000)], priority=P_DEPS)
    p.join("lessons", [(None, "T-9", "l" * 4000)], priority=P_LESSONS, empty="")
    assert p.tokens() > 3000
    p.fit(1500)                                                     # shortening is enough
    st = {(x["name"], x["source"]): x["status"] for x in p.manifest()["sections"]}
    assert st[("lessons", "T-9")] == "cut" and st[("criteria", None)] == "included" and p.tokens() <= 1500
    p.fit(350)                                                      # now parts must go
    m = p.manifest()
    st = {(x["name"], x["source"]): x["status"] for x in m["sections"]}
    assert st[("lessons", "T-9")] == "omitted"                      # lowest priority went first
    assert st[("deps", "T-1")] == "cut" and st[("criteria", None)] == "included"
    assert p.tokens() <= 350 and m["budget"] == 350 and m["omitted"] == 1
    assert "left out to fit the context budget" in p.values["lessons"]


def test_required_cuttable_part_is_last_resort():
    p = ContextPack("verify")
    p.add("criteria", "c" * 200, required=True)
    p.add("output", "o" * 40_000, required=True, cuttable=True)
    p.fit(2000)
    out = [s for s in p.manifest()["sections"] if s["name"] == "output"][0]
    assert out["status"] == "cut" and out["tokens"] >= 500                        # kept MIN_REQUIRED_CHARS
    assert [s for s in p.manifest()["sections"] if s["name"] == "criteria"][0]["status"] == "included"


def test_budget_is_capped_by_the_model_window(monkeypatch):
    assert budget_for("execute", "claude-haiku-5-5") == 24_000
    assert budget_for("execute", "claude-haiku-5-5", 0.5) == 12_000
    monkeypatch.setattr("forgeone.context.budget.context_window", lambda m: 8192)
    assert budget_for("execute", "qwen3-small") == int(8192 * 0.6)


def test_agent_conversation_budget_shortens_recent_results_too():
    llm = RecordingLLM(pages=5)
    t = run_agent(SPEC, "q", RunConfig(model="claude-haiku-5-5", retry_backoff_s=0,
                                       history=HistoryPolicy(keep_full=5, max_tokens=2500)),
                  browser=LongBrowser(), llm=llm)
    assert t.context["shortened_results"] >= 3                     # keep_full=5 alone would shorten none
    assert t.context["calls"][-1]["tokens_est"] <= 2500 + 1600     # within budget, give or take the newest page


# ---------------------------------------------------------------- step 4: memory
def test_attempt_history_reaches_the_third_attempt(env):  # noqa: F811
    script, store, engine = env
    script.milestones = [MilestoneDraft(title="Only", description="d", done_when=["done"])]
    script.tasks = {"Only": [draft("facts")]}
    script.verify_fail["facts"] = 2
    tree = engine.run(new_goal(engine, autonomy="autonomous").goal.id)
    third = [p for k, p in script.prompts if k == "execute" and "do facts" in p][2]
    assert "What went wrong in earlier attempts" in third and "attempt 1, verify: numbers missing" in third
    ex = [a for a in tree.actions if a.kind == "execute"][2]
    assert any(s["name"] == "earlier attempts" and s["source"] == "attempt 1" for s in ex.context["sections"])


def test_lessons_from_an_earlier_goal_reach_a_similar_task(env):  # noqa: F811
    script, store, engine = env
    script.milestones = [MilestoneDraft(title="Only", description="d", done_when=["done"])]
    script.tasks = {"Only": [draft("Extract sugar limits from the WHO page", type="extract")]}
    script.verify_fail["Extract sugar limits from the WHO page"] = 1
    engine.run(new_goal(engine, autonomy="autonomous").goal.id)
    script.tasks = {"Only": [draft("Extract salt limits from the WHO page", type="extract")]}
    tree = engine.run(new_goal(engine, autonomy="autonomous").goal.id)
    prompt = [p for k, p in script.prompts if k == "execute" and "salt" in p][0]
    assert "Lessons from similar earlier work" in prompt and "numbers missing" in prompt and "It passed after that" in prompt
    ex = [a for a in tree.actions if a.kind == "execute"][0]
    lesson = [s for s in ex.context["sections"] if s["name"] == "lessons from earlier work"][0]
    assert lesson["source"].startswith("T-") and lesson["status"] == "included"


def test_lean_policy_has_no_memory(env):  # noqa: F811
    script, store, engine = env
    script.milestones = [MilestoneDraft(title="Only", description="d", done_when=["done"])]
    script.tasks = {"Only": [draft("Extract sugar limits", type="extract")]}
    script.verify_fail["Extract sugar limits"] = 2
    engine.run(new_goal(engine, autonomy="autonomous").goal.id)
    tree = engine.run(new_goal(engine, autonomy="autonomous", context_policy="lean").goal.id)
    third = [p for k, p in script.prompts if k == "execute"][-1]
    assert "earlier attempts" not in third and "Lessons" not in third
    ex = [a for a in tree.actions if a.kind == "execute"][-1]
    assert ex.context["policy"] == "lean" and ex.context["budget"] == 12_000
    assert any("lessons" in e["what"] for e in ex.context["excluded"])


def test_your_rejection_ranks_first():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    row = dict(goal="G", task_status="done", started_at=now)
    rows = [{**row, "task_id": "T-1", "title": "Write the newsletter intro", "kind": "verify", "actor_type": "ai",
             "reason": "too long", "feedback": "shorten it"},
            {**row, "task_id": "T-2", "title": "Write the pricing note", "kind": "approve", "actor_type": "human",
             "reason": "rejected", "feedback": "use this year's prices"},
            {**row, "task_id": "T-3", "title": "Translate the menu", "kind": "verify", "actor_type": "ai",
             "reason": "wrong language", "feedback": "Dutch, not German"}]
    got = find_lessons(rows, "Write the newsletter outro")
    assert [x.task_id for x in got] == ["T-2", "T-1"]               # unrelated T-3 is not a lesson
    assert "you rejected it" in got[0].text


# ---------------------------------------------------------------- step 5: policies and variants
def test_policies_and_overrides():
    assert resolve(None) is PRESETS["default"] and resolve("rich").budget_scale == 1.5
    p = resolve({"base": "lean", "keep_full": 2})
    assert p.name == "lean+custom" and p.keep_full == 2 and p.lessons is False
    assert resolve({"base": "rich", "name": "rich-nocache", "cache": False}).name == "rich-nocache"
    h = resolve("lean").history("claude-haiku-5-5")
    assert h.keep_full == 1 and h.max_tokens == 20_000
    with pytest.raises(ValueError):
        resolve("huge")
    with pytest.raises(ValueError):
        resolve({"base": "lean", "temperature": 1})


def test_variants_carry_a_context_policy():
    v = Variant(name="lean-ctx", model="claude-haiku-5-5", context={"base": "lean", "keep_full": 2})
    assert v.context_policy.keep_full == 2
    assert v.fingerprint != Variant(name="lean-ctx", model="claude-haiku-5-5").fingerprint
    with pytest.raises(Exception):
        Variant(name="x", context="enormous")
