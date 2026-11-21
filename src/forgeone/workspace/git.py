"""Thin, explicit git wrapper. Every call fails loudly with git's own message."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

FORGEONE_AUTHOR = ["-c", "user.name=ForgeOne", "-c", "user.email=forgeone@localhost"]


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def is_repo(path: Path) -> bool:
    return (path / ".git").exists()


def current_branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def has_commits(repo: Path) -> bool:
    return bool(git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False))


def dirty(repo: Path) -> list[str]:
    return [l for l in git(repo, "status", "--porcelain").splitlines() if l.strip()]


def has_remote(repo: Path) -> bool:
    return "origin" in git(repo, "remote").split()


def remote_url(repo: Path) -> str | None:
    return git(repo, "remote", "get-url", "origin", check=False) or None


def github_web_url(remote: str | None) -> str | None:
    """git@github-voyagerone:owner/Repo.git or https://github.com/owner/Repo -> https://github.com/owner/Repo"""
    if not remote:
        return None
    m = re.match(r"^(?:git@|ssh://git@)?(?P<host>[^:/]+)[:/](?P<path>[^/]+/[^/]+?)(?:\.git)?/?$", remote) \
        or re.match(r"^https?://(?P<host>[^/]+)/(?P<path>[^/]+/[^/]+?)(?:\.git)?/?$", remote)
    if not m or "github" not in m.group("host"):
        return None
    return f"https://github.com/{m.group('path')}"
