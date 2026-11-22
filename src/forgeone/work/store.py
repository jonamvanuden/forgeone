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
    def failures(self, task_type: str, exclude_task: str | None = None, limit: int = 200) -> list[dict]: ...


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

    def failures(self, task_type: str, exclude_task: str | None = None, limit: int = 200) -> list[dict]:
        """Failed checks and rejections (with a reason or fix) of tasks of this type, newest first."""
        rows = []
        for a in self.rows[Action].values():
            t = self.rows[Task].get(a.task_id) if a.task_id else None
            if (t is None or t.type != task_type or t.id == exclude_task or a.status != "failed"
                    or a.kind not in ("verify", "edge_test", "approve") or a.input.get("gate")
                    or not (a.feedback or a.reason)):
                continue
            rows.append(_failure_row(self.rows[Goal][a.goal_id], t, a))
        return sorted(rows, key=lambda r: r["started_at"], reverse=True)[:limit]

    def find(self, record_id: str):
        for cls, kind in ((Goal, "goal"), (Milestone, "milestone"), (Task, "task"), (Action, "action")):
            r = self.rows[cls].get(record_id)
            if r is not None:
                return kind, (r.id if cls is Goal else r.goal_id)
        return None


def _failure_row(goal: Goal, t: Task, a: Action) -> dict:
    return {"goal_id": goal.id, "goal": goal.title, "task_id": t.id, "title": t.title, "type": t.type,
            "task_status": t.status, "kind": a.kind, "actor_type": a.actor_type, "reason": a.reason,
            "feedback": a.feedback, "failure_reason": a.failure_reason, "started_at": a.started_at}


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

    def failures(self, task_type: str, exclude_task: str | None = None, limit: int = 200) -> list[dict]:
        with self._conn(rows=True) as c:
            return c.execute("""
                SELECT g.id AS goal_id, g.title AS goal, t.id AS task_id, t.title, t.type, t.status AS task_status,
                       a.kind, a.actor_type, a.reason, a.feedback, a.failure_reason, a.started_at
                FROM actions a JOIN tasks t ON t.id = a.task_id JOIN goals g ON g.id = a.goal_id
                WHERE t.type = %(type)s AND t.id <> %(exclude)s AND a.status = 'failed'
                  AND a.kind IN ('verify', 'edge_test', 'approve') AND NOT coalesce((a.input->>'gate')::boolean, false)
                  AND (coalesce(a.feedback, '') <> '' OR coalesce(a.reason, '') <> '')
                ORDER BY a.started_at DESC LIMIT %(limit)s""",
                {"type": task_type, "exclude": exclude_task or "", "limit": limit}).fetchall()

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
