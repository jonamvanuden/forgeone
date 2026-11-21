"""Builder prompts, one file per node, so prompt changes are versioned and diffable like code.

Placeholders use str.format syntax: {goal}, {plan}, {tools}.
"""
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").rstrip("\n")


def render(name: str, **values) -> str:
    return load(name).format(**values)
