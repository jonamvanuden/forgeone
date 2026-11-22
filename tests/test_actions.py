"""Starting work from the Control Room: password, limits, and the command it runs."""
import asyncio
import json
import time

import pytest

from forgeone.config import settings
from forgeone.control_room import actions
from forgeone.control_room.actions import ActionError

TOKEN = "a-long-enough-password-123"


@pytest.fixture(autouse=True)
def runs(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGEONE_RUNS_DIR", str(tmp_path / "runs"))
    settings.cache_clear()
    yield
    settings.cache_clear()


@pytest.fixture
def act(monkeypatch):
    monkeypatch.setenv("FORGEONE_ACTION_TOKEN", TOKEN)
    monkeypatch.setenv("FORGEONE_MAX_GOAL_BUDGET", "2")
    monkeypatch.setattr(actions, "agents", lambda: ["voyagerone"])
    actions._failures.clear()
    started = []
    monkeypatch.setattr(actions, "_start", lambda kind, args, gid, title, extra=None:
                        started.append((kind, args, gid, title)) or {"id": "J-00000000", "goal_id": gid})
    return started


def test_password_is_required_and_checked(act, monkeypatch):
    actions.check_token(TOKEN)
    with pytest.raises(ActionError) as e:
        actions.check_token("wrong-password-but-long")
    assert e.value.status == 401
    with pytest.raises(ActionError) as e:
        actions.check_token(None)
    assert e.value.status == 401
    monkeypatch.setenv("FORGEONE_ACTION_TOKEN", "short")              # too short: switched off
    assert not actions.enabled()
    with pytest.raises(ActionError) as e:
        actions.check_token("short")
    assert e.value.status == 403


def test_too_many_wrong_passwords_lock_out(act):
    for _ in range(10):
        with pytest.raises(ActionError):
            actions.check_token("nope")
    with pytest.raises(ActionError) as e:
        actions.check_token(TOKEN)                                    # even the right one, for a while
    assert e.value.status == 429
    actions._failures[:] = [time.time() - 700] * 10                   # 10 minutes later
    actions.check_token(TOKEN)


def test_new_goal_runs_the_same_command_as_the_cli(act):
    job = actions.new_goal({"title": "What does WHO advise on free sugars?", "description": "in grams",
                            "criteria": "a summary\n\nwith a source", "constraints": ["Only WHO"],
                            "agent": "voyagerone", "budget": "1.5", "autonomy": "autonomous", "context": "rich"})
    kind, args, gid, _ = act[0]
    assert kind == "goal" and job["goal_id"] == gid and gid.startswith("G-")
    assert args[:5] == ["goal", "new", "What does WHO advise on free sugars?", "--id", gid]
    for pair in (["--budget", "1.5"], ["--agent", "voyagerone"], ["--autonomy", "autonomous"], ["--context", "rich"],
                 ["--description", "in grams"], ["--constraint", "Only WHO"]):
        i = args.index(pair[0])
        assert args[i:i + 2] == pair
    assert args.count("--criteria") == 2 and args[-1] == "--run"


@pytest.mark.parametrize("body, msg", [
    ({"title": "x", "budget": "5"}, "at most $2.00"),
    ({"title": "x"}, "set a budget"),
    ({"title": "", "budget": 1}, "Title is required"),
    ({"title": "x", "budget": 1, "agent": "someone-else"}, "Unknown agent"),
    ({"title": "x", "budget": 1, "autonomy": "yolo"}, "autonomy must be"),
    ({"kind": "build", "agent": "voyagerone", "budget": 1, "publish": "main"}, "branch or not at all"),
    ({"kind": "build", "budget": 1}, "Choose an agent"),
])
def test_limits(act, body, msg):
    with pytest.raises(ActionError) as e:
        actions.new_goal(body)
    assert e.value.status == 400 and msg.lower() in e.value.message.lower()
    assert not act


def test_build_uses_the_repo_goal_or_the_given_instructions(act):
    actions.new_goal({"kind": "build", "agent": "voyagerone", "budget": 0.5})
    _, args, gid, title = act[0]
    assert args == ["build", "--agent", "voyagerone", "--publish", "branch", "--budget", "0.5", "--goal-id", gid]
    assert title == "Build voyagerone"
    actions.new_goal({"kind": "build", "agent": "voyagerone", "budget": 0.5, "publish": "none",
                      "description": "Build a discovery agent that cites WHO."})
    _, args, gid, _ = act[1]
    f = args[args.index("--goal") + 1]
    assert "--publish" in args and args[args.index("--publish") + 1] == "none"
    assert open(f, encoding="utf-8").read().startswith("Build a discovery agent")


def test_a_job_runs_forgeone_in_the_background(tmp_path):
    assert str(actions.jobs_dir()).startswith(str(tmp_path))
    job = actions._start("goal", ["--help"], None, "help")
    assert job["state"] == "running" and job["command"] == "forgeone --help"
    actions._procs[job["id"]].wait(timeout=30)
    for _ in range(50):                                               # the reaper writes the exit code
        if json.loads((actions.jobs_dir() / f"{job['id']}.json").read_text()).get("exit_code") is not None:
            break
        time.sleep(0.1)
    out = actions.job_log(job["id"])
    assert out["state"] == "finished" and any("usage" in line for line in out["log"])
    assert actions.list_jobs()[0]["id"] == job["id"]
    with pytest.raises(ActionError):
        actions.job_log("../../etc/passwd")


def test_one_job_per_goal_and_at_most_two(monkeypatch):
    monkeypatch.setattr(actions, "list_jobs", lambda limit=20: [{"id": "J-1", "state": "running", "goal_id": "G-aaaaaa"}])
    with pytest.raises(ActionError) as e:
        actions._start("run", ["goal", "run", "G-aaaaaa"], "G-aaaaaa", "x")
    assert e.value.status == 409
    monkeypatch.setattr(actions, "list_jobs", lambda limit=20: [{"id": f"J-{i}", "state": "running"} for i in range(2)])
    with pytest.raises(ActionError) as e:
        actions._start("goal", ["--help"], None, "x")
    assert "already running" in e.value.message


def test_post_without_password_is_refused(act):
    from forgeone.control_room import server

    class Req:
        method = "POST"
        headers = {"content-length": "2"}
        path_params = {}

        async def json(self):
            return {"title": "x", "budget": 1}

    resp = asyncio.run(server._act(lambda r, b: actions.new_goal(b))(Req()))
    assert resp.status_code == 401 and not act
    Req.headers = {"content-length": "2", "x-forgeone-token": TOKEN}
    resp = asyncio.run(server._act(lambda r, b: actions.new_goal(b))(Req()))
    assert resp.status_code == 200 and act
