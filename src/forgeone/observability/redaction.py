"""Secret redaction: nothing that looks like a key, token or password ever reaches disk."""
from __future__ import annotations

import re
from typing import Any

_SECRETS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)([\"']?\s*[:=]\s*[\"']?)[^\s\"',]{6,}"),
]


def redact(text: str) -> str:
    for pat in _SECRETS:
        text = pat.sub(lambda m: (m.group(1) + m.group(2) if m.lastindex else "") + "[REDACTED]", text)
    return text


def redact_obj(obj: Any) -> Any:
    """Redact every string inside a structure BEFORE serializing (keeps JSON valid)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj
