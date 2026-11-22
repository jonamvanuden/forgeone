"""Lessons: what went wrong before on similar work, so the next attempt does not repeat it.

A lesson comes from a failed check or a rejection of a task of the same type (in this goal or
an earlier one): the reason, the fix that was asked for, and whether the task passed afterwards.
Your own rejection notes rank first; then by how similar the titles are; then by recency.
Extractive and deterministic: no model call, every lesson points to its source task.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STOP = set("a an and are as at be by for from in into is it of on or the this to with what which who".split())


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in STOP}


def similarity(a: str, b: str) -> float:
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


@dataclass
class Lesson:
    task_id: str
    text: str
    score: float


def find_lessons(rows: list[dict], title: str, description: str = "", limit: int = 3, min_similarity: float = 0.1) -> list[Lesson]:
    """rows: failures from WorkStore.failures(), each with title, kind, actor_type, reason, feedback,
    task_status, goal, task_id, started_at."""
    scored, seen = [], set()
    for r in sorted(rows, key=lambda r: r["started_at"], reverse=True):
        fix = (r.get("feedback") or "").strip()
        why = (r.get("reason") or "").strip()
        key = (fix or why).lower()
        if not key or key in seen:
            continue
        human = r["actor_type"] == "human"
        sim = max(similarity(title, r["title"]), 0.6 * similarity(description, r["title"]))
        if sim < min_similarity and not human:
            continue
        seen.add(key)
        who = "you rejected it" if human else f"{r['kind'].replace('_', ' ')} failed"
        text = (f'Similar task "{r["title"]}" ({r["goal"]}): {who}: {why or fix}.'
                + (f" Asked to fix: {fix}." if fix and fix != why else "")
                + (" It passed after that." if r.get("task_status") == "done" else ""))
        scored.append(Lesson(r["task_id"], text, sim + (1.0 if human else 0.0) + (0.2 if r.get("task_status") == "done" else 0)))
    return sorted(scored, key=lambda x: -x.score)[:limit]
