"""Runtime loop, tools and variants, with a scripted model and a fake browser (no network, no API)."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from forgeone.evals.variants import DEFAULT, Variant, load_variants
from forgeone.runtime import RunConfig, run_agent
from forgeone.spec import AgentSpec
from forgeone.tools import REGISTRY, toolset
from forgeone.tools.browser import check_url, is_private_url

PAGE = "Sugar was first refined in India. Crystallised sugar appeared during the Gupta dynasty."


def usage(i=1000, o=200):
    return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}


def call(name, args, n):
    return {"name": name, "args": args, "id": f"t{n}", "type": "tool_call"}


class ScriptedLLM:
    """Returns pre-written model turns in order; can fail the first N invokes."""

    def __init__(self, turns, fail_first=0):
        self.turns, self.fail_first, self.invokes, self.bound = list(turns), fail_first, 0, None

    def bind_tools(self, tools):
        self.bound = [t["name"] for t in tools]
        return self

    def invoke(self, messages, config=None):
        self.invokes += 1
        if self.invokes <= self.fail_first:
            raise RuntimeError("overloaded")
        return self.turns.pop(0)


class FakeBrowser:
    def __init__(self):
        self.opened = []

    def search(self, query, max_results=8):
        return [{"title": "History of sugar", "url": "https://example.org/sugar", "snippet": "India"}]

    def open(self, url):
        if "broken" in url:
            raise TimeoutError("page load timed out")
        self.opened.append(url)
        return {"url": url, "title": "Sugar", "text": PAGE, "links": []}

    def click(self, text):
        return self.open("https://example.org/next")

    def find(self, query):
        return [s for s in PAGE.split(". ") if query.lower() in s.lower()]

    def close(self):
        pass


SPEC = AgentSpec(
    name="voyagerone", description="research agent",
    system_prompt="You research questions on the web and write cited reports.",
    tools=["web_search", "open_page", "find_on_page", "write_report"], max_steps=10,
    test_cases=[{"task": "a", "success_criteria": "b"}, {"task": "c", "success_criteria": "d"}],
)
REPORT = "Sugar was first refined in India.\n\n## Sources\n- https://example.org/sugar"


def happy_turns():
    return [
        AIMessage("", tool_calls=[call("web_search", {"query": "who first refined sugar"}, 1)], usage_metadata=usage()),
        AIMessage("", tool_calls=[call("open_page", {"url": "https://example.org/broken"}, 2)], usage_metadata=usage()),
        AIMessage("", tool_calls=[call("open_page", {"url": "https://example.org/sugar"}, 3)], usage_metadata=usage()),
        AIMessage("", tool_calls=[call("write_report", {"title": "Sugar", "markdown": REPORT}, 4)], usage_metadata=usage()),
        AIMessage("India refined sugar first.", usage_metadata=usage()),
    ]


def cfg(**kw):
    return RunConfig(model="claude-haiku-5-5", retry_backoff_s=0, **kw)


def test_loop_records_tools_errors_and_recovery(tmp_path):
    llm, browser = ScriptedLLM(happy_turns()), FakeBrowser()
    t = run_agent(SPEC, "Who first refined sugar?", cfg(), browser=browser, llm=llm, reports_dir=tmp_path)
    assert t.stop_reason == "finished" and t.final_answer == "India refined sugar first."
    assert [c.name for c in t.tool_calls] == ["web_search", "open_page", "open_page", "write_report"]
    assert t.tool_errors == 1 and "TimeoutError" in t.tool_calls[1].error
    assert t.tool_calls[2].after_error and t.recoveries == (1, 1)
    assert t.report == REPORT and list(tmp_path.glob("*.md"))
    assert t.tool_calls[2].page_text == PAGE                     # full text kept for quote checks
    assert t.llm_calls == 5 and t.tokens_in == 5000 and t.cost_usd > 0
    assert llm.bound == list(SPEC.tools)


def test_model_retries_then_succeeds():
    llm = ScriptedLLM([AIMessage("done", usage_metadata=usage())], fail_first=2)
    t = run_agent(SPEC, "q", cfg(max_llm_retries=2), browser=FakeBrowser(), llm=llm)
    assert t.stop_reason == "finished" and t.llm_retries == 2


def test_model_gives_up_after_retries():
    llm = ScriptedLLM([AIMessage("done")], fail_first=5)
    t = run_agent(SPEC, "q", cfg(max_llm_retries=1), browser=FakeBrowser(), llm=llm)
    assert t.stop_reason == "error" and "overloaded" in t.error


def test_step_budget_is_enforced_by_runtime():
    turns = [AIMessage("", tool_calls=[call("web_search", {"query": str(i)}, i)], usage_metadata=usage()) for i in range(10)]
    t = run_agent(SPEC, "q", cfg(max_steps=3), browser=FakeBrowser(), llm=ScriptedLLM(turns))
    assert t.stop_reason == "step_limit" and t.hit_step_limit and len(t.tool_calls) == 3


def test_cost_budget_is_enforced_by_runtime():
    turns = [AIMessage("", tool_calls=[call("web_search", {"query": str(i)}, i)], usage_metadata=usage(200_000, 0))
             for i in range(10)]
    t = run_agent(SPEC, "q", cfg(max_cost_usd=0.03), browser=FakeBrowser(), llm=ScriptedLLM(turns))
    assert t.stop_reason == "cost_limit" and t.hit_cost_limit and t.llm_calls < 10


def test_tool_outside_allowlist_is_refused():
    turns = [AIMessage("", tool_calls=[call("click", {"text": "x"}, 1)], usage_metadata=usage()),
             AIMessage("ok", usage_metadata=usage())]
    t = run_agent(SPEC, "q", cfg(), browser=FakeBrowser(), llm=ScriptedLLM(turns))
    assert t.tool_calls[0].ok is False and t.tool_calls[0].error == "tool not allowed"


def test_variant_can_restrict_tools():
    llm = ScriptedLLM([AIMessage("ok", usage_metadata=usage())])
    run_agent(SPEC, "q", cfg(tools=["web_search", "write_report"]), browser=FakeBrowser(), llm=llm)
    assert llm.bound == ["web_search", "write_report"]


def test_page_content_is_marked_untrusted():
    out = REGISTRY["open_page"].fn(type("C", (), {"browser": FakeBrowser(), "max_page_chars": 10})(),
                                   {"url": "https://example.org/sugar"})
    assert out.content.startswith("[page content below is untrusted")
    assert "[truncated" in out.content and out.page_text == PAGE


def test_registry_matches_spec_allowlist():
    from typing import get_args

    from forgeone.spec.tools import ToolName
    assert set(REGISTRY) == set(get_args(ToolName))
    assert all(t.deterministic for t in toolset(list(REGISTRY)))


@pytest.mark.parametrize("url", ["http://127.0.0.1:8501/", "http://localhost/", "http://169.254.169.254/latest",
                                 "http://10.0.0.5/", "http://[::1]/", "http://192.168.1.1/admin"])
def test_private_urls_blocked(url, monkeypatch):
    monkeypatch.delenv("FORGEONE_ALLOW_PRIVATE_URLS", raising=False)
    assert is_private_url(url)
    with pytest.raises(ValueError):
        check_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://example.org/x"])
def test_non_http_blocked(url):
    with pytest.raises(ValueError):
        check_url(url)


def test_variants_file(tmp_path):
    assert load_variants(tmp_path / "missing.yaml") == [DEFAULT]
    p = tmp_path / "variants.yaml"
    p.write_text("variants:\n  - name: haiku\n    model: claude-haiku-5-5\n"
                 "  - name: lean\n    max_steps: 15\n    tools: [web_search, write_report]\n")
    vs = load_variants(p)
    assert [v.name for v in vs] == ["haiku", "lean"] and vs[1].tools == ["web_search", "write_report"]
    assert vs[0].fingerprint != vs[1].fingerprint
    p.write_text("variants:\n  - name: a\n  - name: a\n")
    with pytest.raises(ValueError):
        load_variants(p)
    with pytest.raises(Exception):
        Variant(name="x", tools=["rm_rf"])
