"""Attribution logger: who did what, when, and via which pool.

Every node should call :meth:`AttributionLogger.log_decision` after it
produces output. Records are appended as JSON lines to ``logs/attribution.jsonl``
so they can be tailed or ingested by an analytics pipeline.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

# Ensure logs directory is importable.
_DEFAULT_LOG = Path(__file__).resolve().parent.parent / "logs" / "attribution.jsonl"
_DEFAULT_LOG.parent.mkdir(parents=True, exist_ok=True)


class AttributionLogger:
    """Thread-safe JSON-line attribution logger."""

    def __init__(self, log_path: Path | None = None) -> None:
        self.log_path: Path = Path(log_path) if log_path else _DEFAULT_LOG
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # In-memory buffer for session-level summaries.
        self._buffer: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def log_decision(
        self,
        *,
        session_id: str,
        agent: str,
        node: str,
        pool: str | None = None,
        decision: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a single attribution record and return it."""
        record: dict[str, Any] = {
            "ts": time.time(),
            "session_id": session_id,
            "agent": agent,
            "node": node,
            "pool": pool,
            "decision": decision,
            "metadata": metadata or {},
        }
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._buffer.append(record)
        return record

    # ------------------------------------------------------------------
    # Reading / summarising
    # ------------------------------------------------------------------
    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the last ``n`` records from the JSONL file."""
        if not self.log_path.exists():
            return []
        with self.log_path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
        out: list[dict[str, Any]] = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def get_session_summary(self, session_id: str) -> dict[str, Any]:
        """Aggregate on-disk records for one session (buffer is just a cache)."""
        records = [r for r in self.tail(10000) if r.get("session_id") == session_id]
        by_agent: dict[str, int] = {}
        by_pool: dict[str, int] = {}
        for r in records:
            by_agent[r["agent"]] = by_agent.get(r["agent"], 0) + 1
            if r.get("pool"):
                by_pool[r["pool"]] = by_pool.get(r["pool"], 0) + 1
        return {
            "session_id": session_id,
            "total": len(records),
            "by_agent": by_agent,
            "by_pool": by_pool,
            "first_ts": records[0]["ts"] if records else None,
            "last_ts": records[-1]["ts"] if records else None,
        }

    def clear_buffer(self) -> None:
        with self._lock:
            self._buffer.clear()


_SINGLETON: AttributionLogger | None = None


def get_attribution_logger() -> AttributionLogger:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = AttributionLogger()
    return _SINGLETON


def set_attribution_logger(logger: AttributionLogger) -> None:
    """Replace the singleton (used by tests)."""
    global _SINGLETON
    _SINGLETON = logger


__all__ = ["AttributionLogger", "get_attribution_logger", "set_attribution_logger"]
