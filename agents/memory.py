"""Three-layer memory helpers (working / episodic / semantic).

* Working memory: an in-process dict, lives only for one process.
* Episodic memory: SQLite-backed, optional TTL.
* Semantic memory: best-effort ChromaDB client (degrades gracefully when
  the package is not installed).

All helpers are synchronous so they can be called from LangGraph nodes
without extra async plumbing.

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
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


# ----------------------------------------------------------------------
# Working memory
# ----------------------------------------------------------------------
class WorkingMemory:
    """Thread-safe dict-like working memory for one process."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)


# ----------------------------------------------------------------------
# Episodic memory (SQLite + TTL)
# ----------------------------------------------------------------------
class EpisodicMemory:
    """Append-only episodic memory with a per-record TTL."""

    def __init__(self, db_path: Path, default_ttl: float = 7 * 24 * 3600.0) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    metadata TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_episodes_key ON episodes(key)")
            conn.commit()

    def store_episode(
        self,
        key: str,
        value: Any,
        *,
        ttl: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        ttl = self.default_ttl if ttl is None else float(ttl)
        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO episodes(key, value, created_at, expires_at, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    key,
                    json.dumps(value, ensure_ascii=False),
                    now,
                    now + ttl,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def recall(self, key: str, limit: int = 10) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT id, value, created_at, expires_at, metadata "
                "FROM episodes WHERE key=? AND expires_at > ? "
                "ORDER BY id DESC LIMIT ?",
                (key, now, limit),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(row[1])
                meta = json.loads(row[4] or "{}")
            except json.JSONDecodeError:
                value, meta = row[1], {}
            out.append(
                {
                    "id": row[0],
                    "value": value,
                    "created_at": row[2],
                    "expires_at": row[3],
                    "metadata": meta,
                }
            )
        return out


# ----------------------------------------------------------------------
# Semantic memory (ChromaDB, optional)
# ----------------------------------------------------------------------
class SemanticMemory:
    """Best-effort wrapper around ChromaDB.

    If ``chromadb`` is not installed the class still works but every call
    becomes a no-op and ``is_available()`` returns ``False``.
    """

    def __init__(self, persist_dir: Path, collection: str = "team_memory") -> None:
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection
        self._client = None
        self._collection = None
        try:
            import chromadb  # type: ignore

            self._client = chromadb.PersistentClient(path=str(self.persist_dir))
            self._collection = self._client.get_or_create_collection(collection)
        except Exception:
            self._client = None
            self._collection = None

    def is_available(self) -> bool:
        return self._collection is not None

    def add(self, doc_id: str, text: str, metadata: dict[str, Any] | None = None) -> None:
        if self._collection is None:
            return
        self._collection.add(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata or {}],
        )

    def query(self, text: str, k: int = 5) -> list[dict[str, Any]]:
        if self._collection is None:
            return []
        res: dict[str, Any] = dict(self._collection.query(query_texts=[text], n_results=k))

        def _first(key: str) -> list[Any]:
            # Chroma returns one list per query text (we send one) and any
            # field may be ``None`` when it was not requested / not stored.
            outer = res.get(key)
            if not outer:
                return []
            inner = outer[0]
            return list(inner) if inner else []

        docs = _first("documents")
        ids = _first("ids")
        metas = _first("metadatas")
        dists = _first("distances")
        out: list[dict[str, Any]] = []
        for i, doc in enumerate(docs):
            out.append(
                {
                    "id": ids[i] if i < len(ids) else None,
                    "document": doc,
                    "metadata": (metas[i] if i < len(metas) else None) or {},
                    "distance": dists[i] if i < len(dists) else None,
                }
            )
        return out


# ----------------------------------------------------------------------
# Convenience accessors
# ----------------------------------------------------------------------
def store_episode(key: str, value: Any, **kwargs: Any) -> int:
    from .memory import _default_episodic  # local import to avoid cycles

    return _default_episodic().store_episode(key, value, **kwargs)


def recall_similar(key: str, limit: int = 10) -> list[dict[str, Any]]:
    from .memory import _default_episodic

    return _default_episodic().recall(key, limit=limit)


def semantic_search(text: str, k: int = 5) -> list[dict[str, Any]]:
    from .memory import _default_semantic

    return _default_semantic().query(text, k=k)


def _default_root() -> Path:
    return Path(__file__).resolve().parent.parent / "memory"


_EPISODIC: EpisodicMemory | None = None
_SEMANTIC: SemanticMemory | None = None
_WORKING: WorkingMemory | None = None


def _default_episodic() -> EpisodicMemory:
    global _EPISODIC
    if _EPISODIC is None:
        _EPISODIC = EpisodicMemory(_default_root() / "episodic.sqlite")
    return _EPISODIC


def _default_semantic() -> SemanticMemory:
    global _SEMANTIC
    if _SEMANTIC is None:
        _SEMANTIC = SemanticMemory(_default_root() / "semantic")
    return _SEMANTIC


def get_working_memory() -> WorkingMemory:
    global _WORKING
    if _WORKING is None:
        _WORKING = WorkingMemory()
    return _WORKING


__all__ = [
    "WorkingMemory",
    "EpisodicMemory",
    "SemanticMemory",
    "store_episode",
    "recall_similar",
    "semantic_search",
    "get_working_memory",
]
