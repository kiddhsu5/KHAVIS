"""Dispatch history for the web dashboard.

Each fan-out writes one JSON line to ``LOG_DIR/dispatches.jsonl``. The
dashboard reads the tail of that file — no database, no daemon. Deletes
rewrite the file under the same lock; they do not touch workspace files.
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


def _same_ts(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-4
    except (TypeError, ValueError):
        return False


def _prompt_matches(stored: Any, needle: str) -> bool:
    if not needle:
        return True
    text = str(stored or "").strip()
    probe = needle.strip()[:120]
    if not probe:
        return True
    return text.startswith(probe) or probe.startswith(text[:80]) or probe[:40] in text


def delete_events(
    items: list[dict[str, Any]] | None = None,
    *,
    clear: bool = False,
) -> dict[str, int]:
    """Remove dispatch lines. Does not delete workspace files.

    ``clear`` drops every line. Otherwise each item needs ``ts``. A unique
    timestamp is enough. If several lines share that timestamp, ``prompt``
    and ``thread`` pick one; still-ambiguous lines are left in place.
    """
    path = history_path()
    specs = [item for item in (items or []) if isinstance(item, dict)]
    if not clear and not specs:
        raise ValueError("沒有要刪除的紀錄")

    with _LOCK:
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        except OSError as exc:
            raise ValueError("讀不到歷史檔") from exc

        parsed: list[tuple[str, dict[str, Any] | None]] = []
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed.append((line, json.loads(stripped)))
            except json.JSONDecodeError:
                parsed.append((line, None))

        if clear:
            removed = len(parsed)
            _rewrite_history(path, [])
            return {"removed": removed, "kept": 0, "ambiguous": 0}

        drop: set[int] = set()
        ambiguous = 0
        for spec in specs:
            if spec.get("ts") is None:
                raise ValueError("刪除需要 ts")
            hits = [
                i
                for i, (_, event) in enumerate(parsed)
                if isinstance(event, dict) and _same_ts(event.get("ts"), spec.get("ts"))
            ]
            if len(hits) > 1:
                narrowed = []
                for i in hits:
                    event = parsed[i][1]
                    assert isinstance(event, dict)
                    thread = str(spec.get("thread") or "").strip()
                    if thread and str(event.get("thread") or "") != thread:
                        continue
                    if not _prompt_matches(event.get("prompt"), str(spec.get("prompt") or "")):
                        continue
                    narrowed.append(i)
                hits = narrowed
            if len(hits) == 1:
                drop.add(hits[0])
            elif len(hits) > 1:
                ambiguous += 1

        kept_lines = [line for i, (line, _) in enumerate(parsed) if i not in drop]
        _rewrite_history(path, kept_lines)
        return {"removed": len(drop), "kept": len(kept_lines), "ambiguous": ambiguous}


def _rewrite_history(path: Path, lines: list[str]) -> None:
    body = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
