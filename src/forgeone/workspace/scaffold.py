"""Create a new agent repository from templates: `forgeone agent init <name> --path <dir>`."""
from __future__ import annotations

from pathlib import Path

import yaml

from . import git as g

TEMPLATES = Path(__file__).parent / "templates"
FILES = {  # template -> path in the new repo
    "goal.md": "goal.md",
    "README.md": "README.md",
    "gitignore.txt": ".gitignore",
    "evals/suite.yaml": "evals/suite.yaml",
    "agent/README.md": "agent/README.md",
}


def scaffold(name: str, path: Path, description: str = "", owner: str = "", remote: str | None = None,
             branch: str = "main") -> Path:
    path = Path(path)
    if path.exists() and any(p for p in path.iterdir() if p.name != ".git"):
        raise FileExistsError(f"{path} exists and is not empty")
    values = {"name": name, "title": name[:1].upper() + name[1:], "owner": owner or "unknown",
              "description": description or f"{name}, an agent built by ForgeOne."}
    for src, dst in FILES.items():
        out = path / dst
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text((TEMPLATES / src).read_text(encoding="utf-8").format(**values), encoding="utf-8")
    manifest = {"name": name, "description": values["description"], "owner": values["owner"]}
    (path / "forgeone.yaml").write_text("# Agent manifest, read by ForgeOne.\n"
                                        + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    if not g.is_repo(path):
        g.git(path, "init", "-q", "-b", branch)
    if remote and not g.has_remote(path):
        g.git(path, "remote", "add", "origin", remote)
    g.git(path, "add", "-A")
    g.git(path, *g.FORGEONE_AUTHOR, "commit", "-q", "-m", f"Create agent {name} from the ForgeOne template")
    return path
