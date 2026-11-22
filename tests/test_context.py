"""Context management: the manifest on every model call, shortening the agent's history,
prompt caching and cache-aware prices."""
from __future__ import annotations

import copy

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from test_runtime import SPEC, FakeBrowser, call, usage
from test_work import draft, env, new_goal  # noqa: F401  (env is a fixture)

from forgeone.context import ContextPack, HistoryPolicy, estimate_tokens
from forgeone.llm.pricing import call_cost
from forgeone.runtime import RunConfig, run_agent
from forgeone.work.schemas import MilestoneDraft

LONG = "WHO recommends less than 10% of energy from free sugars. " * 100      # ~5,800 characters


def test_pack_records_sections_cuts_and_exclusions():
    p = ContextPack("verify", "claude-haiku-5-5")
    p.add("criteria", "1. a\n2. b", required=True)
    p.add("output", "x" * 1000, limit=100, source="execute attempt 1")
    p.join("deps", [("## 1.1", "T-1", "y" * 50), ("## 1.2", "T-2", "z" * 500)], limit_each=200)
    p.exclude("the executor's reasoning", "the reviewer judges the output")
    m = p.manifest("instructions " * 10, "verify@abc")
    names = [s["name"] for s in m["sections"]]
    assert names == ["instructions", "criteria", "output", "deps", "deps"]
    out = m["sections"][2]
    assert out["status"] == "cut" and out["original_tokens"] == 250 and out["source"] == "execute attempt 1"
    assert m["cut"] == 2 and m["tokens_lost"] == (250 - out["tokens"]) + (125 - m["sections"][4]["tokens"])
    assert [s["source"] for s in m["sections"][3:]] == ["T-1", "T-2"]
    assert m["excluded"][0]["rule"] == "the reviewer judges the output"
    assert "more characters cut" in p.values["output"] and p.values["deps"].startswith("## 1.1")
    assert estimate_tokens("abcde") == 2


def test_every_engine_call_has_a_manifest(env):  # noqa: F811
    script, store, engine = env
    script.milestones = [MilestoneDraft(title="Only", description="d", done_when=["done"])]
    script.tasks = {"Only": [draft("facts"), draft("summary", deps=[1])]}
    script.verify_fail["summary"] = 1
    tree = engine.run(new_goal(engine, autonomy="autonomous").goal.id)
    facts, summary = tree.tasks_of(tree.active_milestones()[0].id)
    assert all(a.context.get("sections") for a in tree.actions if a.actor_type == "ai")
    ex = [a for a in tree.actions_of(summary.id) if a.kind == "execute"]
    dep = [s for s in ex[0].context["sections"] if s["name"] == "dependency output"]
    assert dep[0]["source"] == facts.id and dep[0]["tokens"] > 0
    feedback = [s for s in ex[1].context["sections"] if s["name"] == "feedback"][0]
    assert feedback["status"] == "included" and feedback["source"] == "attempt 1"
    assert ex[0].context["tokens_billed"] == 1000 and ex[0].context["purpose"] == "execute"
    assert {e["what"] for e in ex[0].context["excluded"]} >= {"outputs of earlier attempts"}
    verify = [a for a in tree.actions_of(summary.id) if a.kind == "verify"][0]
    assert any("reasoning" in e["what"] for e in verify.context["excluded"])
    assert verify.context["sections"][0]["name"] == "instructions" and verify.context["sections"][0]["source"].startswith("verify@")
    plan = [a for a in tree.actions if a.kind == "plan"][0]
    assert plan.context["purpose"] == "plan_milestones"


class RecordingLLM:
    """Opens a long page on every step; records what each call was sent."""

    def __init__(self, pages: int):
        self.turns = [AIMessage("", tool_calls=[call("open_page", {"url": f"https://example.org/{i}"}, i)],
                                usage_metadata=usage()) for i in range(pages)]
        self.turns.append(AIMessage("done", usage_metadata=usage()))
        self.sent = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        self.sent.append(copy.deepcopy(messages))
        return self.turns.pop(0)


class LongBrowser(FakeBrowser):
    def open(self, url):
        return {"url": url, "title": "Sugar", "text": LONG, "links": []}


def test_agent_history_keeps_only_recent_results_in_full():
    llm = RecordingLLM(pages=6)
    t = run_agent(SPEC, "q", RunConfig(model="claude-haiku-5-5", retry_backoff_s=0), browser=LongBrowser(), llm=llm)
    last = llm.sent[-1]
    tool_msgs = [m for m in last if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 6
    assert sum("Shortened to save tokens" in m.content for m in tool_msgs) == 3            # the 3 oldest
    assert all(len(m.content) > 1200 for m in tool_msgs[-3:])                              # the 3 newest in full
    assert "open_page(url=https://example.org/0)" in tool_msgs[0].content
    c = t.context
    assert c["shortened_results"] == 3 and c["sent_tokens_est"] < c["without_shortening_est"]
    assert c["calls"][-1]["shortened_results"] == 3 and c["policy"]["keep_full"] == 3
    assert all(tc.page_text == LONG for tc in t.tool_calls)                                 # checks still see everything


def test_history_policy_can_keep_everything():
    llm = RecordingLLM(pages=4)
    t = run_agent(SPEC, "q", RunConfig(model="claude-haiku-5-5", retry_backoff_s=0,
                                       history=HistoryPolicy(keep_full=100)), browser=LongBrowser(), llm=llm)
    assert t.context["shortened_results"] == 0 and t.context["sent_tokens_est"] == t.context["without_shortening_est"]


@pytest.mark.parametrize("model,cached", [("claude-haiku-5-5", True), ("gpt-x", False)])
def test_system_prompt_is_marked_for_caching(model, cached):
    llm = RecordingLLM(pages=0)
    run_agent(SPEC, "q", RunConfig(model=model, retry_backoff_s=0), browser=FakeBrowser(), llm=llm)
    content = llm.sent[0][0].content
    assert isinstance(content, list) == cached
    if cached:
        assert content[0]["cache_control"] == {"type": "ephemeral"} and SPEC.system_prompt in content[0]["text"]


def test_cache_tokens_are_priced_and_counted():
    plain = call_cost("claude-sonnet-5-5", 10_000, 0)
    assert call_cost("claude-sonnet-5-5", 10_000, 0, cache_read=10_000) == pytest.approx(plain * 0.10)
    assert call_cost("claude-sonnet-5-5", 10_000, 0, cache_write=10_000) == pytest.approx(plain * 1.25)

    class CachedLLM(RecordingLLM):
        def invoke(self, messages, config=None):
            ai = super().invoke(messages, config)
            ai.usage_metadata = {"input_tokens": 3000, "output_tokens": 100, "total_tokens": 3100,
                                 "input_token_details": {"cache_read": 2000, "cache_creation": 0}}
            return ai

    t = run_agent(SPEC, "q", RunConfig(model="claude-haiku-5-5", retry_backoff_s=0), browser=LongBrowser(),
                  llm=CachedLLM(pages=1))
    assert t.cache_read_tokens == 4000 and t.context["cache_read_tokens"] == 4000
    assert t.cost_usd == pytest.approx(2 * call_cost("claude-haiku-5-5", 3000, 100, 2000, 0), abs=1e-6)
