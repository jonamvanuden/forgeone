"""Which agents ForgeOne manages, and where their repositories are: agents.yaml in the ForgeOne root.

    agents:
      voyagerone:
        path: ../VoyagerOne                                   # local checkout, relative to ForgeOne
        remote: git@github-voyagerone:jonamvanuden/VoyagerOne.git
        branch: main
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from ..config import settings


class AgentEntry(BaseModel):
    name: str
    path: Path
    remote: str | None = None
    branch: str = "main"


def registry_file() -> Path:
    return settings().home / "agents.yaml"


def load_registry(path: Path | None = None) -> dict[str, AgentEntry]:
    p = path or registry_file()
    if not p.exists():
        return {}
    raw = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("agents") or {}
    out = {}
    for name, cfg in raw.items():
        cfg = cfg or {}
        rp = Path(cfg.get("path", f"../{name}"))
        out[name] = AgentEntry(name=name, path=rp if rp.is_absolute() else (p.parent / rp).resolve(),
                               remote=cfg.get("remote"), branch=cfg.get("branch", "main"))
    return out


def get_agent(name: str) -> AgentEntry:
    reg = load_registry()
    if name not in reg:
        known = ", ".join(sorted(reg)) or "none"
        raise KeyError(f"agent '{name}' is not in {registry_file()} (known: {known}). "
                       f"Add it there, or create one with `forgeone agent init {name} --path ../{name}`.")
    return reg[name]
