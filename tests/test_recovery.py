"""Pick up a goal where it stopped: retry a failed task, survive an interrupted run."""
from __future__ import annotations

from datetime import timedelta

import pytest
from test_work import env, new_goal  # noqa: F401  (fixture)

from forgeone.work.model import now
from forgeone.work.recovery import CannotRetry, interrupted, retry_task


def test_retry_continues_from_the_failed_task(env):  # noqa: F811
    script, store, engine = env
    script.verify_fail["facts list"] = 99
    gid = new_goal(engine).goal.id
    tree = engine.run(gid)
    assert tree.goal.status == "failed"
    facts, summary = tree.tasks_of(tree.active_milestones()[0].id)
    executes_before = sum(1 for k, _ in script.prompts if k == "execute")

    script.verify_fail["facts list"] = 0
    msg = retry_task(store, gid, facts.code, note="use the WHO fact sheet")
    assert "reset" in msg
    tree = store.load(gid)
    facts, summary = tree.tasks_of(tree.active_milestones()[0].id)
    assert facts.status == "pending" and facts.max_attempts == facts.attempts + 3
    assert summary.status == "pending"                                    # unblocked
    assert tree.active_milestones()[0].status == "pending" and tree.goal.status == "paused"

    tree = engine.run(gid)
    facts = next(t for t in tree.tasks if t.title == "facts list")
    assert facts.status == "done" and facts.attempts == 4                 # the old attempts stay in the log
    hint = [p for k, p in script.prompts if k == "execute" and "do facts list" in p][-1]
    assert "use the WHO fact sheet" in hint
    assert sum(1 for k, _ in script.prompts if k == "execute") > executes_before
    assert tree.active_milestones()[0].status in ("done", "waiting")      # supervised: waits for your approval


def test_only_failed_tasks_can_be_retried(env):  # noqa: F811
    script, store, engine = env
    gid = new_goal(engine, autonomy="autonomous").goal.id
    tree = engine.run(gid)
    t = tree.tasks[0]
    with pytest.raises(CannotRetry):
        retry_task(store, gid, t.code)
    with pytest.raises(CannotRetry):
        retry_task(store, gid, "9.9")


def test_interrupted_run_is_picked_up(env):  # noqa: F811
    script, store, engine = env
    gid = new_goal(engine, autonomy="autonomous").goal.id
    tree = store.load(gid)
    t = tree.tasks[0]
    t.status, t.attempts, t.max_attempts = "running", 3, 3                 # the process died mid-attempt
    tree.goal.status = "running"
    store.save(t, tree.goal)
    tree = store.load(gid)
    assert not interrupted(tree)                                         # just now: could still be running
    for x in [tree.goal, *tree.tasks]:
        x.updated_at = now() - timedelta(hours=1)
    for a in tree.actions:
        a.started_at = a.finished_at = now() - timedelta(hours=1)
    assert interrupted(tree)
    tree = engine.run(gid)
    t = tree.task(t.id)
    assert t.status == "done" and t.attempts == 4 and tree.goal.status == "done"
