"""AgentRepo: one agent's own git repository, and the only way ForgeOne touches it.

Ownership inside an agent repo:
    forgeone.yaml       manifest                       owner
    goal.md             what to build                  owner
    evals/              the eval suite                 owner ONLY: the builder never reads or writes it
    agent/              what ForgeOne produced         ForgeOne, via publish() on a branch
Publishing writes only inside agent/; anything else staged aborts the commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from . import git as g

OWNED_BY_FORGEONE = "agent/"


class WorkspaceError(RuntimeError):
    pass


@dataclass
class PublishResult:
    mode: str
    branch: str | None = None
    commit: str | None = None
    pushed: bool = False
    url: str | None = None
    push_error: str | None = None
    unchanged: bool = False     # the spec is identical to what the base branch already has


class AgentRepo:
    def __init__(self, name: str, path: Path, base_branch: str = "main"):
        self.name, self.path, self.base_branch = name, Path(path), base_branch

    @classmethod
    def for_agent(cls, name: str) -> "AgentRepo":
        from .registry import get_agent

        e = get_agent(name)
        return cls(e.name, e.path, e.branch)

    # ---- layout -------------------------------------------------------------
    @property
    def goal_path(self) -> Path:
        return self.path / "goal.md"

    @property
    def suite_path(self) -> Path:
        return self.path / "evals" / "suite.yaml"

    @property
    def spec_path(self) -> Path:
        return self.path / "agent" / "spec.yaml"

    def goal(self) -> str:
        if not self.goal_path.exists():
            raise WorkspaceError(f"{self.goal_path} not found")
        return self.goal_path.read_text(encoding="utf-8")

    # ---- safety checks before spending tokens ---------------------------------
    def check_ready(self, pull: bool = True) -> list[str]:
        """Raise if the repo cannot receive a build; return warnings (e.g. pull failed)."""
        warnings: list[str] = []
        if not g.is_repo(self.path):
            raise WorkspaceError(f"{self.path} is not a git repository. Clone it first "
                                 f"(see docs/OPERATIONS.md) or run `forgeone agent init {self.name}`.")
        if not g.has_commits(self.path):
            raise WorkspaceError(f"{self.path} has no commits yet")
        if (b := g.current_branch(self.path)) != self.base_branch:
            raise WorkspaceError(f"{self.path} is on branch '{b}', expected '{self.base_branch}'")
        if d := g.dirty(self.path):
            raise WorkspaceError(f"{self.path} has uncommitted changes: {d[:5]}")
        if pull and g.has_remote(self.path):
            try:
                g.git(self.path, "pull", "--ff-only", "-q")
            except g.GitError as e:
                warnings.append(f"could not pull {self.name}: {e}")
        self.goal()
        return warnings

    # ---- publishing a build -----------------------------------------------------
    def publish(self, run_id: str, files: dict[str, str], message: str, mode: str = "branch",
                primary: str = "agent/spec.yaml") -> PublishResult:
        """Commit `files` (paths relative to the repo, all under agent/) and push.

        If `primary` is among the files and is unchanged, nothing is published (a new BUILD.md
        alone is not a new version).
        mode: "branch" -> new branch forgeone/<run_id>, pushed; merge it to approve (default)
              "main"   -> commit straight onto the base branch and push
              "none"   -> do not touch the repo
        """
        if mode == "none":
            return PublishResult(mode="none")
        if mode not in ("branch", "main"):
            raise ValueError(f"unknown publish mode {mode!r}")
        for rel in files:
            if not rel.startswith(OWNED_BY_FORGEONE) or ".." in Path(rel).parts:
                raise WorkspaceError(f"ForgeOne may only write inside {OWNED_BY_FORGEONE}, not {rel}")
        if g.dirty(self.path):
            raise WorkspaceError(f"{self.path} changed during the build; not publishing")

        branch = f"forgeone/{run_id}" if mode == "branch" else self.base_branch
        if mode == "branch":
            g.git(self.path, "checkout", "-q", "-b", branch)
        try:
            for rel, content in files.items():
                p = self.path / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            g.git(self.path, "add", "--", *files.keys())
            staged = g.git(self.path, "diff", "--cached", "--name-only").splitlines()
            outside = [s for s in staged if not s.startswith(OWNED_BY_FORGEONE)]
            if outside:
                raise WorkspaceError(f"refusing to commit files outside {OWNED_BY_FORGEONE}: {outside}")
            if not staged or (primary in files and primary not in staged):
                # identical to the base branch: nothing to review, not an error
                g.git(self.path, "reset", "-q", "--hard", check=False)
                if mode == "branch":
                    g.git(self.path, "checkout", "-q", self.base_branch, check=False)
                    g.git(self.path, "branch", "-q", "-D", branch, check=False)
                return PublishResult(mode=mode, unchanged=True)
            g.git(self.path, *g.FORGEONE_AUTHOR, "commit", "-q", "-m", message)
            res = PublishResult(mode=mode, branch=branch, commit=g.git(self.path, "rev-parse", "--short", "HEAD"))
            if g.has_remote(self.path):
                try:
                    g.git(self.path, "push", "-q", "-u", "origin", branch)
                    res.pushed = True
                except g.GitError as e:
                    res.push_error = str(e)
            web = g.github_web_url(g.remote_url(self.path))
            if web:
                res.url = (f"{web}/compare/{self.base_branch}...{branch}?expand=1" if mode == "branch"
                           else f"{web}/commit/{res.commit}")
            return res
        except Exception:
            # leave the repo exactly as we found it (it was verified clean before writing)
            g.git(self.path, "reset", "-q", "--hard", check=False)
            g.git(self.path, "clean", "-q", "-fd", "--", OWNED_BY_FORGEONE, check=False)
            if mode == "branch":
                g.git(self.path, "checkout", "-q", self.base_branch, check=False)
                g.git(self.path, "branch", "-q", "-D", branch, check=False)
            raise
        finally:
            if mode == "branch":
                g.git(self.path, "checkout", "-q", self.base_branch, check=False)

    def manifest(self) -> dict:
        p = self.path / "forgeone.yaml"
        return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
