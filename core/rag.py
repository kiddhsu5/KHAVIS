"""RAG connector layer (v0.3.0).

A thin adapter interface over Chroma / Qdrant / pgvector. Each backend
is optional — import-guarded, so the core stays dependency-light. When
the library is missing the adapter reports ``available: False`` and the
router continues without retrieval.

Usage::

    from core.rag import get_store
    store = get_store()          # from config/rag.yaml
    if store.available():
        hits = store.search("how do I add a provider", k=3)
        prompt = store.inject_context(prompt, hits)
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml


@dataclass
class Document:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


class VectorStore(Protocol):
    name: str

    def available(self) -> bool: ...
    def search(self, query: str, *, k: int = 3) -> list[Document]: ...
    def add(self, texts: list[str], metadatas: list[dict[str, Any]] | None = None) -> int: ...


@dataclass
class NullStore:
    """Fallback when no backend is configured / importable."""

    name: str = "null"
    reason: str = "no RAG backend configured"

    def available(self) -> bool:
        return False

    def search(self, query: str, *, k: int = 3) -> list[Document]:
        return []

    def add(self, texts: list[str], metadatas: list[dict[str, Any]] | None = None) -> int:
        return 0


class ChromaStore:
    name = "chroma"

    def __init__(self, collection: str = "khavis", persist_dir: str | None = None) -> None:
        self.collection_name = collection
        self.persist_dir = persist_dir or os.environ.get("KHAVIS_RAG_DIR", ".rag/chroma")
        self._client = None

    def _coll(self):
        if self._client is None:
            import chromadb  # noqa: PLC0415

            self._client = chromadb.PersistentClient(path=self.persist_dir)
        return self._client.get_or_create_collection(self.collection_name)

    def available(self) -> bool:
        try:
            self._coll()
            return True
        except Exception:  # noqa: BLE001
            return False

    def search(self, query: str, *, k: int = 3) -> list[Document]:
        coll = self._coll()
        res = coll.query(query_texts=[query], n_results=k)
        # chroma returns one list per query text; we only ever send one.
        docs: list[Document] = []
        texts = (res.get("documents") or [[]])[0] if res.get("documents") else []
        metas = (res.get("metadatas") or [[]])[0] if res.get("metadatas") else []
        dists = (res.get("distances") or [[]])[0] if res.get("distances") else []
        for i, text in enumerate(texts):
            score = 1.0 - float(dists[i]) if i < len(dists) else 0.0
            docs.append(
                Document(text=str(text), metadata=metas[i] if i < len(metas) else {}, score=score)
            )
        return docs

    @staticmethod
    def doc_id(text: str, metadata: dict[str, Any] | None = None) -> str:
        """Stable id for one chunk, derived from its *identity*, not its bytes.

        ``upsert`` replaces by id, so the id has to name the chunk's slot in
        its source document. Hashing the text gives a brand-new id every time
        that document is edited, so re-ingesting a revised file never
        overwrites the chunk it supersedes — the old copy accumulates in the
        collection and keeps being handed back by ``search()`` for content the
        user has already deleted.

        Identity comes from the metadata when the caller supplied one: an
        explicit ``doc_id``/``id``, otherwise ``source`` together with
        ``chunk``. Only then is the hash taken from the identity alone — mixing
        the rest of the metadata in would reintroduce the bug, since a "modified"
        timestamp or a revision counter changes on every edit. With no usable
        identity we fall back to hashing text *and* metadata, which still
        separates genuinely different documents but cannot tell that two
        differing texts once occupied the same slot.
        """
        meta = metadata or {}
        identity: str | None = None
        for key in ("doc_id", "id"):
            if meta.get(key):
                identity = f"{key}={meta[key]}"
                break
        if identity is None and meta.get("source") and meta.get("chunk") is not None:
            identity = f"source={meta['source']}\x00chunk={meta['chunk']}"
        h = hashlib.sha256()
        if identity is not None:
            h.update(identity.encode("utf-8"))
        else:
            h.update(text.encode("utf-8"))
            if meta:
                h.update(b"\0")
                h.update(json.dumps(meta, sort_keys=True, default=str).encode("utf-8"))
        return f"doc-{h.hexdigest()[:32]}"

    def delete_source(self, source: str) -> int:
        """Drop every chunk previously ingested from ``source``.

        Identity-based ids replace a document's chunks one slot at a time, so
        they handle "the same file, edited". They cannot handle re-chunking,
        where the old slot numbering no longer lines up with the new one. A
        caller about to re-ingest a document wholesale should call this first.
        """
        coll = self._coll()
        existing = coll.get(where={"source": source}, include=[])
        ids = [str(i) for i in (existing.get("ids") or [])]
        if not ids:
            return 0
        coll.delete(ids=ids)
        return len(ids)

    def add(self, texts: list[str], metadatas: list[dict[str, Any]] | None = None) -> int:
        if not texts:
            return 0
        metas = list(metadatas) if metadatas else [{} for _ in texts]
        if len(metas) != len(texts):
            raise ValueError("metadatas must have the same length as texts")
        # De-duplicate within the batch too: chroma rejects duplicate ids.
        unique: dict[str, tuple[str, dict[str, Any]]] = {}
        for text, meta in zip(texts, metas, strict=True):
            unique.setdefault(self.doc_id(text, meta), (text, meta))
        coll = self._coll()
        kwargs: dict[str, Any] = {
            "ids": list(unique),
            "documents": [t for t, _m in unique.values()],
        }
        # Recent chroma versions reject empty metadata dicts; only send them
        # when at least one document actually carries metadata.
        if any(m for _t, m in unique.values()):
            kwargs["metadatas"] = [m or {"source": "unknown"} for _t, m in unique.values()]
        coll.upsert(**kwargs)
        return len(unique)


class QdrantStore:
    name = "qdrant"

    def __init__(self, collection: str = "khavis", url: str | None = None) -> None:
        self.collection_name = collection
        self.url = url or os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
        self._client = None

    def _client_or_none(self):
        if self._client is None:
            from qdrant_client import QdrantClient  # noqa: PLC0415

            self._client = QdrantClient(url=self.url)
        return self._client

    def available(self) -> bool:
        try:
            self._client_or_none().get_collections()
            return True
        except Exception:  # noqa: BLE001
            return False

    def search(self, query: str, *, k: int = 3) -> list[Document]:
        # Requires a configured embedding — qdrant is optional and often
        # used with fastembed; keep a clear failure rather than half-work.
        raise NotImplementedError(
            "QdrantStore.search requires embeddings; use ChromaStore for local RAG"
        )

    def add(self, texts: list[str], metadatas: list[dict[str, Any]] | None = None) -> int:
        raise NotImplementedError("QdrantStore.add requires embeddings")


class PgvectorStore:
    name = "pgvector"

    def __init__(self, dsn: str | None = None, table: str = "khavis_docs") -> None:
        self.dsn = dsn or os.environ.get("PGVECTOR_DSN", "")
        self.table = table

    def available(self) -> bool:
        if not self.dsn:
            return False
        try:
            import psycopg  # noqa: PLC0415

            with psycopg.connect(self.dsn, connect_timeout=2) as conn:
                conn.execute("select 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    def search(self, query: str, *, k: int = 3) -> list[Document]:
        raise NotImplementedError("PgvectorStore.search requires embeddings")

    def add(self, texts: list[str], metadatas: list[dict[str, Any]] | None = None) -> int:
        raise NotImplementedError("PgvectorStore.add requires embeddings")


def inject_context(prompt: str, hits: list[Document], *, max_chars: int = 2000) -> str:
    """Prefix the prompt with retrieved context (for non-RAG-aware models)."""
    if not hits:
        return prompt
    blocks = []
    used = 0
    for i, d in enumerate(hits, 1):
        chunk = d.text.strip()
        if not chunk:
            continue
        if used + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - used)]
        used += len(chunk)
        blocks.append(f"[{i}] {chunk}")
        if used >= max_chars:
            break
    ctx = "\n\n".join(blocks)
    return (
        "Use the following context if relevant. If the context is insufficient, say so.\n\n"
        f"{ctx}\n\n---\n\n{prompt}"
    )


def load_store(config_path: Path | str | None = None) -> VectorStore:
    """Pick a store from ``config/rag.yaml`` (`backend: chroma|qdrant|pgvector|null`)."""
    if config_path is None:
        from .env import config_dir  # noqa: PLC0415 - avoid import at module load

        config_path = config_dir() / "rag.yaml"
    data: dict[str, Any] = {}
    p = Path(config_path)
    if p.exists():
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            data = {}
    backend = str(data.get("backend") or os.environ.get("KHAVIS_RAG_BACKEND") or "null").lower()
    opts = data.get(backend) or {}
    if backend == "chroma":
        return ChromaStore(
            collection=str(opts.get("collection", "khavis")),
            persist_dir=opts.get("persist_dir"),
        )
    if backend == "qdrant":
        return QdrantStore(
            collection=str(opts.get("collection", "khavis")),
            url=opts.get("url"),
        )
    if backend == "pgvector":
        return PgvectorStore(dsn=opts.get("dsn"), table=str(opts.get("table", "khavis_docs")))
    return NullStore()


_STORE: VectorStore | None = None


def get_store() -> VectorStore:
    global _STORE  # noqa: PLW0603
    if _STORE is None:
        _STORE = load_store()
    return _STORE


def reset_store(store: VectorStore | None = None) -> VectorStore:
    global _STORE  # noqa: PLW0603
    _STORE = store if store is not None else load_store()
    return _STORE
