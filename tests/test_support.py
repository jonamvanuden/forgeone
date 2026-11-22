"""Support analysis: failures, harder than planned, calibration, could-run-without-AI, missed checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from test_work import Script, draft, env, new_goal  # noqa: F401  (env is a fixture)

from forgeone.work import decide
from forgeone.work.schemas import MilestoneDraft
from forgeone.work.support import analyze


def one_milestone(script, tasks):
    script.milestones = [MilestoneDraft(title="Only", description="d", done_when=["done"])]
    script.tasks = {"Only": tasks}


def trees(store):
    return [store.load(g.id) for g in store.goals()]


def test_failures_harder_than_planned_and_calibration(env):  # noqa: F811
    script, store, engine = env
    one_milestone(script, [draft("collect prices", type="extract"), draft("normalize prices", deps=[1]),
                           draft("summary", type="write")])
    script.verify_fail = {"collect prices": 99, "summary": 1}
    engine.run(new_goal(engine).goal.id)                                           # supervised: no automatic replan
    r = analyze(trees(store))
    failed = {x["title"]: x for x in r["failed"]}
    assert failed["collect prices"]["failure_reason"] == "criteria_not_met" and failed["collect prices"]["attempts"] == 3
    assert failed["normalize prices"]["status"] == "blocked"
    assert "replan" in failed["collect prices"]["next"] and r["kpis"]["failed"] == 2
    hard = {x["title"]: x for x in r["hard"]}
    assert set(hard) == {"summary"} and hard["summary"]["attempts"] == 2 and hard["summary"]["gap"] == 2.5
    assert "numbers missing" in hard["summary"]["why"]
    wrong = {x["reason"]: x for x in r["what_goes_wrong"]}
    assert wrong["criteria_not_met"]["n"] == 4 and wrong["criteria_not_met"]["kind"] == "quality"
    assert wrong["criteria_not_met"]["stages"] == {"verify": 4}
    cal = {c["type"]: c for c in r["calibration"]}
    assert cal["extract"]["failed"] == 1 and cal["extract"]["verdict"] == "harder than predicted"
    assert r["kpis"]["wasted_usd"] > 0 and r["kpis"]["retry_rate"] == 1.0          # both closed tasks retried


def test_could_run_without_ai(env):  # noqa: F811
    script, store, engine = env
    conv = draft("Convert grams to teaspoons")
    conv.automatable, conv.automation_hint = True, "divide by 4.2"
    for _ in range(3):
        one_milestone(script, [conv, draft("Write the note", type="write")])
        engine.run(new_goal(engine, autonomy="autonomous").goal.id)
    r = analyze(trees(store))
    w = r["without_ai"]
    assert len(w["flagged"]) == 1 and w["flagged"][0]["runs"] == 3 and w["flagged"][0]["hint"] == "divide by 4.2"
    rep = {x["title"]: x for x in w["repeated"]}
    assert rep["Convert grams to teaspoons"]["runs"] == 3 and rep["Convert grams to teaspoons"]["goals"] == 3
    idle = {(c["type"], c["stage"]): c for c in w["idle_checks"]}
    assert idle[("write", "verify")]["runs"] == 6 and idle[("write", "verify")]["caught"] == 0
    assert ("milestone", "approve") not in idle                                    # only 3 runs: not enough yet
    assert r["kpis"]["without_ai"] == len(w["flagged"]) + len(w["repeated"]) + len(w["idle_checks"])


def test_missed_by_ai_checks_and_filters(env):  # noqa: F811
    script, store, engine = env
    one_milestone(script, [draft("press release", risk="high")])
    gid = new_goal(engine).goal.id
    tree = engine.run(gid)
    t = tree.tasks_of(tree.active_milestones()[0].id)[0]
    decide(store, t.id, False, "wrong year in the headline")
    r = analyze(trees(store))
    assert r["missed"][0]["note"] == "wrong year in the headline"
    assert set(r["missed"][0]["passed_by"]) == {"verify", "edge_test"}
    assert analyze(trees(store), scope="builds")["goals"] == 0                     # not a build
    later = datetime.now(timezone.utc) + timedelta(days=40)
    assert analyze(trees(store), days=30, now=later)["missed"] == []              # outside the window
    assert analyze(trees(store), days=None, now=later)["missed"]                  # all time


@pytest.mark.parametrize("title,other", [("Convert 3 grams!", "convert grams")])
def test_titles_are_normalized(title, other):
    from forgeone.work.support import _norm
    assert _norm(title) == _norm(other)
