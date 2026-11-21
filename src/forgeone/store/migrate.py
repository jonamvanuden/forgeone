"""Versioned schema migrations.

Every schema change is a new numbered file in migrations/ (002_..., 003_...).
Applied files are recorded in `schema_migrations`; `forgeone db migrate` applies the rest
in order, each in its own transaction. Never edit a migration that has been applied:
add a new one. Docker runs all files once when the database is first created.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings

MIGRATIONS = Path(__file__).parent / "migrations"

_TABLE = """CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"""


def available() -> list[Path]:
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))


def applied(conn) -> set[str]:
    conn.execute(_TABLE)
    conn.commit()
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}


def migrate(dsn: str | None = None) -> list[str]:
    """Apply pending migrations; returns the versions applied now."""
    import psycopg

    done: list[str] = []
    with psycopg.connect(dsn or settings().database_url) as conn:
        have = applied(conn)
        for f in available():
            version = f.stem
            if version in have:
                continue
            conn.execute(f.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            conn.commit()
            done.append(version)
            print(f"  applied {version}")
    print(f"migrations: {len(done)} applied, {len(available()) - len(done)} already up to date")
    return done
