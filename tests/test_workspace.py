"""Agent repositories: registry, scaffold, readiness checks and publishing (local git only)."""
import subprocess

import pytest
import yaml

from forgeone.workspace import AgentRepo, WorkspaceError, load_registry, scaffold
from forgeone.workspace import git as g


def sh(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def agent(tmp_path):
    """A scaffolded agent repo with a local bare 'remote'."""
    remote = tmp_path / "remote.git"
    sh("git", "init", "-q", "--bare", "-b", "main", str(remote))
    path = scaffold("demo", tmp_path / "Demo", description="A demo: with a colon", remote=str(remote))
    g.git(path, "push", "-q", "-u", "origin", "main")
    return AgentRepo("demo", path), remote


def test_scaffold_layout_and_manifest(agent):
    repo, _ = agent
    for f in ["goal.md", "evals/suite.yaml", "agent/README.md", "README.md", ".gitignore", "forgeone.yaml"]:
        assert (repo.path / f).exists(), f
    assert yaml.safe_load((repo.path / "forgeone.yaml").read_text())["description"] == "A demo: with a colon"
    assert g.dirty(repo.path) == []


def test_registry_resolves_relative_paths(tmp_path):
    (tmp_path / "agents.yaml").write_text("agents:\n  demo:\n    path: ../Demo\n    remote: git@x:y/z.git\n")
    e = load_registry(tmp_path / "agents.yaml")["demo"]
    assert e.path == (tmp_path.parent / "Demo").resolve() and e.branch == "main"


def test_check_ready_refuses_dirty_or_wrong_branch(agent):
    repo, _ = agent
    assert repo.check_ready() == []
    (repo.path / "goal.md").write_text("changed")
    with pytest.raises(WorkspaceError, match="uncommitted"):
        repo.check_ready()
    g.git(repo.path, "checkout", "-q", "--", "goal.md")
    g.git(repo.path, "checkout", "-q", "-b", "other")
    with pytest.raises(WorkspaceError, match="expected 'main'"):
        repo.check_ready()


def test_publish_branch_pushes_and_leaves_main_untouched(agent):
    repo, remote = agent
    main_before = g.git(repo.path, "rev-parse", "main")
    res = repo.publish("run-1", {"agent/spec.yaml": "name: demo\n", "agent/BUILD.md": "# b\n"}, "forgeone: build run-1")
    assert res.mode == "branch" and res.branch == "forgeone/run-1" and res.pushed
    assert g.current_branch(repo.path) == "main" and g.git(repo.path, "rev-parse", "main") == main_before
    assert not (repo.path / "agent" / "spec.yaml").exists()           # only on the branch
    files = sh("git", "--git-dir", str(remote), "show", "--name-only", "--format=", "forgeone/run-1").splitlines()
    assert sorted(files) == ["agent/BUILD.md", "agent/spec.yaml"]
    assert sh("git", "--git-dir", str(remote), "log", "-1", "--format=%an", "forgeone/run-1") == "ForgeOne"


def test_publish_main_mode(agent):
    repo, _ = agent
    res = repo.publish("run-2", {"agent/spec.yaml": "x: 1\n"}, "m", mode="main")
    assert res.branch == "main" and res.pushed and (repo.path / "agent" / "spec.yaml").exists()


@pytest.mark.parametrize("bad", ["evals/suite.yaml", "goal.md", "agent/../goal.md"])
def test_publish_refuses_outside_agent_dir(agent, bad):
    repo, _ = agent
    with pytest.raises(WorkspaceError, match="only write inside agent/"):
        repo.publish("run-3", {bad: "x"}, "m")
    assert g.dirty(repo.path) == [] and g.current_branch(repo.path) == "main"


def test_publish_identical_spec_is_a_clean_noop(agent):
    repo, _ = agent
    repo.publish("run-4", {"agent/spec.yaml": "same\n"}, "m", mode="main")
    res = repo.publish("run-5", {"agent/spec.yaml": "same\n", "agent/BUILD.md": "new run\n"}, "m")
    assert res.unchanged and res.commit is None
    assert g.dirty(repo.path) == [] and "forgeone/run-5" not in g.git(repo.path, "branch")
    assert g.current_branch(repo.path) == "main"


def test_publish_none_touches_nothing(agent):
    repo, _ = agent
    assert repo.publish("run-6", {"agent/spec.yaml": "x"}, "m", mode="none").mode == "none"
    assert not (repo.path / "agent" / "spec.yaml").exists()


@pytest.mark.parametrize("remote,url", [
    ("git@github-voyagerone:jonamvanuden/VoyagerOne.git", "https://github.com/jonamvanuden/VoyagerOne"),
    ("https://github.com/jonamvanuden/voyagerone", "https://github.com/jonamvanuden/voyagerone"),
    ("git@gitlab.com:a/b.git", None),
    (None, None),
])
def test_github_web_url(remote, url):
    assert g.github_web_url(remote) == url
