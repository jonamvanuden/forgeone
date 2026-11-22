"""Persistence for goals, milestones, tasks and actions.

Records are saved whole (upsert by id): the engine changes a model object and saves it.
PgStore is the real one; MemoryStore keeps everything in dicts for tests and dry runs.
"""
from __future__ import annotations

import json
from typing import Protocol

from pydantic import BaseModel

from ..config import settings
from .model import Action, Goal, GoalTree, Milestone, Task, now

TABLES: dict[type, str] = {Goal: "goals", Milestone: "milestones", Task: "tasks", Action: "actions"}


class WorkStore(Protocol):
    def save(self, *records: BaseModel) -> None: ...
    def load(self, goal_id: str) -> GoalTree: ...
    def goals(self) -> list[Goal]: ...
    def find(self, record_id: str) -> tuple[str, str] | None: ...   # -> (kind, goal_id)


class NotFound(KeyError):
    pass


def _touch(rec: BaseModel) -> None:
    if "updated_at" in type(rec).model_fields:
        rec.updated_at = now()


class MemoryStore:
    def __init__(self):
        self.rows: dict[type, dict[str, BaseModel]] = {k: {} for k in TABLES}

    def save(self, *records: BaseModel) -> None:
        for r in records:
            _touch(r)
            self.rows[type(r)][r.id] = r.model_copy(deep=True)

    def load(self, goal_id: str) -> GoalTree:
        if goal_id not in self.rows[Goal]:
            raise NotFound(goal_id)
        of = lambda cls: [r.model_copy(deep=True) for r in self.rows[cls].values() if r.goal_id == goal_id]  # noqa: E731
        return GoalTree(goal=self.rows[Goal][goal_id].model_copy(deep=True), milestones=of(Milestone),
                        tasks=of(Task), actions=of(Action))

    def goals(self) -> list[Goal]:
        return sorted(self.rows[Goal].values(), key=lambda g: g.created_at, reverse=True)

    def find(self, record_id: str):
        for cls, kind in ((Goal, "goal"), (Milestone, "milestone"), (Task, "task"), (Action, "action")):
            r = self.rows[cls].get(record_id)
            if r is not None:
                return kind, (r.id if cls is Goal else r.goal_id)
        return None


class PgStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or settings().database_url
        if not self.dsn:
            raise RuntimeError("DATABASE_URL is not set: goals live in Postgres")

    def _conn(self, rows: bool = False):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.dsn, row_factory=dict_row) if rows else psycopg.connect(self.dsn)

    @staticmethod
    def _value(v):
        if isinstance(v, (list, dict)):
            from psycopg.types.json import Jsonb

            return Jsonb(json.loads(json.dumps(v, default=str)))
        return v

    def save(self, *records: BaseModel) -> None:
        with self._conn() as c:
            for r in records:
                _touch(r)
                table = TABLES[type(r)]
                data = r.model_dump()
                cols = list(data)
                sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in cols if k != "id")
                c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                          f"ON CONFLICT (id) DO UPDATE SET {sets}",
                          [self._value(data[k]) for k in cols])

    def load(self, goal_id: str) -> GoalTree:
        with self._conn(rows=True) as c:
            g = c.execute("SELECT * FROM goals WHERE id = %s", (goal_id,)).fetchone()
            if not g:
                raise NotFound(goal_id)
            sel = lambda t: c.execute(f"SELECT * FROM {t} WHERE goal_id = %s", (goal_id,)).fetchall()  # noqa: E731
            return GoalTree(goal=Goal.model_validate(g),
                            milestones=[Milestone.model_validate(r) for r in sel("milestones")],
                            tasks=[Task.model_validate(r) for r in sel("tasks")],
                            actions=[Action.model_validate(r) for r in sel("actions")])

    def goals(self) -> list[Goal]:
        with self._conn(rows=True) as c:
            return [Goal.model_validate(r) for r in c.execute("SELECT * FROM goals ORDER BY created_at DESC").fetchall()]

    def find(self, record_id: str):
        with self._conn(rows=True) as c:
            r = c.execute("""SELECT 'goal' AS kind, id AS goal_id FROM goals WHERE id = %(id)s
                             UNION ALL SELECT 'milestone', goal_id FROM milestones WHERE id = %(id)s
                             UNION ALL SELECT 'task', goal_id FROM tasks WHERE id = %(id)s
                             UNION ALL SELECT 'action', goal_id FROM actions WHERE id = %(id)s""",
                          {"id": record_id}).fetchone()
        return (r["kind"], r["goal_id"]) if r else None


def default_store() -> WorkStore:
    return PgStore()
