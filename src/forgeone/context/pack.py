"""ContextPack: assemble what one model call sees, section by section, and record a manifest.

Every piece of context goes in through `add` with a name, its source (a task id, a URL) and an
optional character limit. The pack keeps the text for the prompt and a manifest entry: how many
tokens it takes, whether it was cut and how much was lost. Things that are left out on purpose
(the executor's reasoning for a reviewer, the eval suite for the builder) are recorded as
`excluded`, with the rule, so the manifest says not only what went in but what did not.

Token counts are estimates (characters / 4, rounded up): good enough to compare sections and
calls. The real number billed is stored next to it on the action (tokens_in).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str | None) -> int:
    return math.ceil(len(text or "") / CHARS_PER_TOKEN)


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n[... {len(text) - limit} more characters cut]"


@dataclass
class ContextPack:
    purpose: str                               # e.g. "execute", "verify", "plan_tasks", "agent"
    model: str | None = None
    values: dict[str, str] = field(default_factory=dict)
    sections: list[dict] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)

    def add(self, key: str, text, *, name: str | None = None, source: str | None = None,
            limit: int | None = None, required: bool = False, empty: str = "") -> str:
        """Put `text` into the prompt value `key` (cut to `limit` characters) and record it."""
        original = "" if text is None else str(text)
        out = cut(original, limit) if limit and len(original) > limit else original
        self.sections.append({
            "name": name or key, "source": source, "tokens": estimate_tokens(out),
            "status": "cut" if out != original else ("empty" if not original else "included"),
            "original_tokens": estimate_tokens(original), "required": required})
        self.values[key] = out if out else empty
        return self.values[key]

    def join(self, key: str, parts: list[tuple[str, str | None, str]], *, limit_each: int | None = None,
             sep: str = "\n\n", empty: str = "none", name: str | None = None) -> str:
        """Several sources in one prompt value, e.g. the outputs of all dependencies:
        parts = [(heading, source id, text), ...]; each part is its own manifest section."""
        texts = []
        for heading, source, text in parts:
            original = text or ""
            out = cut(original, limit_each) if limit_each and len(original) > limit_each else original
            texts.append(f"{heading}\n{out}")
            self.sections.append({"name": name or key, "source": source, "tokens": estimate_tokens(out),
                                  "status": "cut" if out != original else "included",
                                  "original_tokens": estimate_tokens(original), "required": False})
        self.values[key] = sep.join(texts) if texts else empty
        return self.values[key]

    def fixed(self, key: str, text: str) -> str:
        """A value that is part of the instructions (lists, catalogs), counted as instructions."""
        self.values[key] = text
        self.sections.append({"name": key, "source": None, "tokens": estimate_tokens(text), "status": "included",
                              "original_tokens": estimate_tokens(text), "required": True})
        return text

    def exclude(self, what: str, rule: str) -> None:
        self.excluded.append({"what": what, "rule": rule})

    def manifest(self, instructions: str = "", prompt_version: str | None = None) -> dict:
        ins = estimate_tokens(instructions)
        merged: dict[tuple, dict] = {}                 # parts with the same name and source are one section
        for s in self.sections:
            key = (s["name"], s["source"])
            if key not in merged:
                merged[key] = dict(s)
                continue
            m = merged[key]
            m["tokens"] += s["tokens"]
            m["original_tokens"] += s["original_tokens"]
            m["required"] = m["required"] or s["required"]
            states = {m["status"], s["status"]}
            m["status"] = "cut" if "cut" in states else "included" if "included" in states else "empty"
        secs = ([{"name": "instructions", "source": prompt_version, "tokens": ins, "status": "included",
                  "original_tokens": ins, "required": True}] if instructions else []) + list(merged.values())
        return {
            "purpose": self.purpose, "model": self.model,
            "tokens_est": sum(s["tokens"] for s in secs),
            "cut": sum(1 for s in secs if s["status"] == "cut"),
            "tokens_lost": sum(s["original_tokens"] - s["tokens"] for s in secs if s["status"] == "cut"),
            "sections": secs, "excluded": self.excluded,
        }
