"""Append-only dispatch history for the web dashboard.

Each fan-out writes one JSON line to ``LOG_DIR/dispatches.jsonl``. The
dashboard reads the tail of that file — no database, no daemon.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_MAX_FIELD_CHARS = 2_000


def history_path() -> Path:
    base = os.environ.get("KHAVIS_LOG_DIR") or os.environ.get("BOT_LOG_DIR")
    d = Path(base) if base else Path(__file__).resolve().parent.parent / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "dispatches.jsonl"


def record(event: dict[str, Any]) -> None:
    """Append one dispatch event. Never raises."""
    payload = dict(event)
    payload.setdefault("ts", time.time())
    # Keep the file bounded: trim every user/model-controlled long text field.
    for key in ("prompt", "consensus", "result", "review_details", "error"):
        v = payload.get(key)
        if isinstance(v, str) and len(v) > _MAX_FIELD_CHARS:
            payload[key] = v[:_MAX_FIELD_CHARS] + "…"
    line = json.dumps(payload, ensure_ascii=False, default=str)
    try:
        with _LOCK, history_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # history is best-effort; never break a dispatch over it


def tail(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent events, newest first."""
    path = history_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines[-limit:]):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
