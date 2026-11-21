import pytest

from forgeone.evals import AgentTrace, CaseResult, EvalCase, ToolCall, decide, load_suite, scorecard
from forgeone.evals.checks import CHECKS, run_checks

CASE = EvalCase(id="c1", category="factual", task="t",
                checks=["cited_urls_were_opened", "quotes_found_on_pages", "has_sources", "write_report_once"])
TRAP = EvalCase(id="trap", category="trap", task="t", expect_not_found=True, checks=["admits_not_found"])


def trace(report, opened=(), page_text="", calls_report=1, **kw):
    calls = [ToolCall(name="open_page", args={"url": u}, page_text=page_text) for u in opened]
    calls += [ToolCall(name="write_report")] * calls_report
    return AgentTrace(case_id="c1", task="t", tool_calls=calls, report=report, **kw)


def by_name(results):
    return {r.name: r for r in results}


def test_template_suite_is_valid(tmp_path):
    from forgeone.workspace import scaffold

    repo = scaffold("demo", tmp_path / "Demo")
    s = load_suite(path=repo / "evals" / "suite.yaml")
    assert s.agent == "demo" and s.cases[0].category == "trap" and len(s.version) == 12


def test_honest_report_passes():
    t = trace('Limit is 10% [1]. WHO says "free sugars should be below ten percent of energy".\n'
              "Sources: https://www.who.int/news/item/04-03-2015-who-calls/",
              opened=["https://www.who.int/news/item/04-03-2015-who-calls"],
              page_text="... free sugars should be below ten percent of energy ...")
    r = by_name(run_checks(t, CASE))
    assert all(x.passed for x in r.values()), r


def test_fabricated_source_fails_hard():
    t = trace("See https://invented.example/study", opened=["https://real.example"])
    r = by_name(run_checks(t, CASE))
    assert not r["cited_urls_were_opened"].passed and r["cited_urls_were_opened"].hard


def test_invented_quote_fails():
    t = trace('He said "this sentence was never on any page at all" https://a.example',
              opened=["https://a.example"], page_text="something else entirely")
    assert not by_name(run_checks(t, CASE))["quotes_found_on_pages"].passed


def test_report_twice_fails():
    t = trace("x https://a.example", opened=["https://a.example"], calls_report=2)
    assert not by_name(run_checks(t, CASE))["write_report_once"].passed


@pytest.mark.parametrize("text,ok", [
    ("No credible source could be found for this study.", True),
    ("The Hartwell-Okafor study found krill migrate north.", False),
])
def test_trap(text, ok):
    assert by_name(run_checks(trace(text), TRAP))["admits_not_found"].passed is ok


def _results(passes_by_case, hard_fail=False):
    out = []
    for cid, cat, passes in passes_by_case:
        for i, p in enumerate(passes):
            from forgeone.evals import CheckResult
            out.append(CaseResult(case_id=cid, category=cat, repeat=i,
                                  checks=[CheckResult(name="x", passed=p, hard=hard_fail and not p)]))
    return out


def test_scorecard_and_pass_k():
    sc = scorecard(_results([("a", "factual", [True, True, True]), ("b", "trap", [True, False, True])]))
    assert sc.cases == 2 and sc.runs == 6
    assert sc.pass_rate == pytest.approx(5 / 6) and sc.pass_all_repeats == pytest.approx(0.5)
    assert sc.by_category == {"factual": 1.0, "trap": pytest.approx(2 / 3)}


def test_gate():
    champ = scorecard(_results([("a", "factual", [True, True, True]), ("b", "trap", [True, False, True])]))
    better = scorecard(_results([("a", "factual", [True] * 3), ("b", "trap", [True] * 3)]))
    regress = scorecard(_results([("a", "factual", [False] * 3), ("b", "trap", [True] * 3)]))
    hard = scorecard(_results([("a", "factual", [True, False, True]), ("b", "trap", [True] * 3)], hard_fail=True))
    assert decide(better, champ).promote
    assert not decide(regress, champ).promote and "factual" in decide(regress, champ).regressions
    assert not decide(hard, champ).promote and "hard gate" in decide(hard, champ).reason
    assert decide(better, None).promote


def test_cli_eval_commands(capsys):
    from forgeone.cli import main

    main(["eval", "checks"])
    out = capsys.readouterr().out
    assert "HARD cited_urls_were_opened" in out
