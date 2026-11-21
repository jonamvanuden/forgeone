"""Replay runs/*/events.jsonl into Postgres. Safe to repeat: duplicates are skipped."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..config import settings
from .writer import write_batch


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def events_from_run_dir(run_dir: Path) -> list[tuple[dict, dict | None]]:
    out = []
    lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        ev = json.loads(line)
        # older events had no event_id: derive a stable one from the line itself
        ev.setdefault("event_id", hashlib.sha1(f"{ev.get('run_id')}|{line}".encode()).hexdigest())
        blobs = None
        if ev["event"] in ("llm.response", "llm.error"):
            p, r = ev.get("prompt_payload"), ev.get("payload")
            blobs = {"prompt": _read_json(run_dir / p) if p else None,
                     "response": _read_json(run_dir / r) if r and ev["event"] == "llm.response" else None}
        elif ev["event"] == "artifact.saved" and str(ev.get("path", "")).endswith(".yaml"):
            # step-2 runs logged only a path; turn it into a proper artifact row
            import yaml

            spec_path = Path(ev["path"])
            if not spec_path.is_absolute():
                spec_path = run_dir.parent.parent / spec_path
            if spec_path.exists():
                art = {**ev, "event": "artifact", "kind": "spec",
                       "event_id": ev["event_id"] + ":artifact"}
                body = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
                art["name"] = (body or {}).get("name")
                out.append((ev, None))
                out.append((art, {"body": body}))
                continue
        out.append((ev, blobs))
    return out


def backfill(runs_root: str | Path | None = None, dsn: str | None = None) -> int:
    import psycopg

    root = Path(runs_root) if runs_root else settings().runs_dir
    dirs = sorted(d for d in root.iterdir() if (d / "events.jsonl").exists()) if root.exists() else []
    with psycopg.connect(dsn or settings().database_url) as conn:
        for d in dirs:
            batch = events_from_run_dir(d)
            write_batch(conn, batch)
            print(f"  {d.name}: {len(batch)} events")
    print(f"backfilled {len(dirs)} runs (duplicates skipped)")
    return len(dirs)
