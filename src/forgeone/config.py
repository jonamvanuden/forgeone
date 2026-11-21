"""Central settings. Every path and default lives here, read from the environment.

Nothing else in ForgeOne should call os.getenv for its own settings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def find_home(start: Path | None = None) -> Path:
    """The project root: $FORGEONE_HOME, else the nearest folder with pyproject.toml, else cwd."""
    env = os.getenv("FORGEONE_HOME")
    if env:
        return Path(env).resolve()
    for p in [start or Path.cwd(), *(start or Path.cwd()).parents]:
        if (p / "pyproject.toml").exists() and (p / "src" / "forgeone").exists():
            return p.resolve()
    here = Path(__file__).resolve()
    for p in here.parents:  # running from an editable install inside the repo
        if (p / "pyproject.toml").exists() and (p / "src" / "forgeone").exists():
            return p
    return Path.cwd().resolve()


@dataclass(frozen=True)
class Settings:
    home: Path
    builder_model: str = "claude-haiku-5-5"
    agent_model: str = "claude-haiku-5-5"   # default model for agents ForgeOne built
    database_url: str | None = None
    runs_dir: Path = field(default=Path("runs"))
    docs_dir: Path = field(default=Path("docs"))
    control_room_url: str | None = None   # used for links in agent repos (BUILD.md)


@lru_cache(maxsize=1)
def settings() -> Settings:
    home = find_home()
    load_dotenv(home / ".env")

    def rel(var: str, default: str) -> Path:
        p = Path(os.getenv(var, default))
        return p if p.is_absolute() else home / p

    return Settings(
        home=home,
        builder_model=os.getenv("FORGEONE_MODEL", "claude-haiku-5-5"),
        agent_model=os.getenv("FORGEONE_AGENT_MODEL", "claude-haiku-5-5"),
        database_url=os.getenv("DATABASE_URL"),
        runs_dir=rel("FORGEONE_RUNS_DIR", "runs"),
        docs_dir=rel("FORGEONE_DOCS_DIR", "docs"),
        control_room_url=os.getenv("FORGEONE_CONTROL_ROOM_URL", "https://cashgrab.online").rstrip("/") or None,
    )
