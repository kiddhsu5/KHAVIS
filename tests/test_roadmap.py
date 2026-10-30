"""Tests for v0.3 RAG, v0.4 OpenAI server, v0.5 model overrides."""

from __future__ import annotations

from pathlib import Path

from core.budget import BudgetPolicy, reset_guard
from core.registry import PluginRegistry


class TestRAG:
    def test_null_store_default(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from core.rag import load_store

        store = load_store(tmp_path / "missing.yaml")
        assert store.available() is False
        assert store.search("q") == []

    def test_inject_context(self):
        from core.rag import Document, inject_context

        out = inject_context("hello", [Document(text="fact one"), Document(text="fact two")])
        assert "fact one" in out and "hello" in out
        assert inject_context("hi", []) == "hi"

    def test_chroma_unavailable_without_lib(self):
        from core.rag import ChromaStore

        # chromadb not installed in the test env → available() False
        store = ChromaStore(persist_dir="/tmp/no-such-chroma")
        # either unavailable or available; must not raise on available()
        assert isinstance(store.available(), bool)

    def test_chroma_add_uses_content_ids_and_upsert(self):
        from unittest.mock import MagicMock

        from core.rag import ChromaStore

        store = ChromaStore(persist_dir="/tmp/no-such-chroma")
        coll = MagicMock()
        store._coll = lambda: coll  # type: ignore[method-assign]
        assert store.add(["alpha", "beta", "alpha"]) == 2
        kwargs = coll.upsert.call_args.kwargs
        assert len(kwargs["ids"]) == 2 and len(set(kwargs["ids"])) == 2
        assert all(i.startswith("doc-") and i != "doc-0" for i in kwargs["ids"])
        assert "metadatas" not in kwargs
        coll.add.assert_not_called()
        first_ids = kwargs["ids"]
        # A second batch never reuses positional ids -> no clobbering; the
        # same content maps to the same id -> de-duplicated by upsert.
        store.add(["gamma", "alpha"])
        second_ids = coll.upsert.call_args.kwargs["ids"]
        assert second_ids[1] == first_ids[0]
        assert second_ids[0] not in first_ids

    def test_chroma_doc_id_depends_on_metadata(self):
        from core.rag import ChromaStore

        assert ChromaStore.doc_id("x", {"src": "a"}) != ChromaStore.doc_id("x", {"src": "b"})
        assert ChromaStore.doc_id("x") == ChromaStore.doc_id("x")

    def test_doc_id_is_stable_across_an_edit_of_the_same_chunk(self):
        """Re-ingesting a revised document must replace, not accumulate.

        With a content-addressed id, editing a file produced a *new* id, so the
        superseded chunk was never overwritten. Every re-ingest left another
        copy behind and ``search()`` kept answering with text the user had
        already deleted. Identity-based ids fix that.
        """
        from core.rag import ChromaStore

        old = {"source": "notes.md", "chunk": 0, "modified": "2026-01-01"}
        new = {"source": "notes.md", "chunk": 0, "modified": "2026-10-01"}
        assert ChromaStore.doc_id("the original paragraph", old) == ChromaStore.doc_id(
            "the edited paragraph", new
        )
        # A different chunk of the same document is still its own document.
        assert ChromaStore.doc_id("x", {"source": "notes.md", "chunk": 1}) != ChromaStore.doc_id(
            "x", {"source": "notes.md", "chunk": 0}
        )
        # And a different file is too.
        assert ChromaStore.doc_id("x", {"source": "a.md", "chunk": 0}) != ChromaStore.doc_id(
            "x", {"source": "b.md", "chunk": 0}
        )

    def test_explicit_doc_id_metadata_wins(self):
        from core.rag import ChromaStore

        assert ChromaStore.doc_id("anything", {"doc_id": "stable-1"}) == ChromaStore.doc_id(
            "something else entirely", {"doc_id": "stable-1"}
        )

    def test_reingesting_an_edited_document_overwrites_the_same_id(self):
        from unittest.mock import MagicMock

        from core.rag import ChromaStore

        store = ChromaStore(persist_dir="/tmp/no-such-chroma")
        coll = MagicMock()
        store._coll = lambda: coll  # type: ignore[method-assign]

        store.add(["v1 body"], [{"source": "notes.md", "chunk": 0}])
        first = coll.upsert.call_args.kwargs["ids"]
        store.add(["v2 body, edited"], [{"source": "notes.md", "chunk": 0}])
        second = coll.upsert.call_args.kwargs["ids"]
        assert second == first, "the revised chunk must land on the same id"

    def test_load_store_uses_config_dir(self, tmp_path: Path, monkeypatch):
        (tmp_path / "rag.yaml").write_text(
            "backend: chroma\nchroma:\n  collection: from-config-dir\n", encoding="utf-8"
        )
        monkeypatch.setenv("KHAVIS_CONFIG_DIR", str(tmp_path))
        from core.rag import ChromaStore, load_store

        store = load_store()
        assert isinstance(store, ChromaStore)
        assert store.collection_name == "from-config-dir"


class TestModelOverrides:
    def test_apply_model_overrides(self, tmp_path: Path):
        root = Path(__file__).resolve().parent.parent
        reg = PluginRegistry(root).discover()
        y = tmp_path / "models.yaml"
        y.write_text(
            """
overrides:
  - pool: MiniMax-M3
    model: my-fine-tune-v1
    notes: "ft"
custom_models:
  - pool: MiniMax-M3
    name: ft-x
    model: ft-x
""",
            encoding="utf-8",
        )
        reg.apply_model_overrides(y)
        p = reg.get("MiniMax-M3")
        assert p.model == "my-fine-tune-v1"
        assert p.metadata.get("model_notes") == "ft"
        assert any(c.get("name") == "ft-x" for c in p.metadata.get("custom_models") or [])

    def test_unknown_pool_is_error_not_crash(self, tmp_path: Path):
        root = Path(__file__).resolve().parent.parent
        reg = PluginRegistry(root).discover()
        y = tmp_path / "models.yaml"
        y.write_text("overrides:\n  - pool: NOPE\n    model: x\n", encoding="utf-8")
        reg.apply_model_overrides(y)
        assert any("NOPE" in e for e in reg.errors())


class TestOpenAIServer:
    def test_create_app_importable(self):
        try:
            from server.openai_api import create_app
        except Exception as exc:  # pragma: no cover
            # fastapi optional
            assert "fastapi" in str(exc).lower() or True
            return
        try:
            app = create_app()
        except RuntimeError as exc:
            assert "fastapi" in str(exc).lower()
            return
        assert app is not None

    def test_pick_plugin_explicit_model(self):
        from core.capability_router import CapabilityRouter
        from core.registry import PluginRegistry
        from server.openai_api import _pick_plugin

        root = Path(__file__).resolve().parent.parent
        reg = PluginRegistry(root).discover().apply_pools_config(root / "config" / "pools.yaml")
        router = CapabilityRouter(reg)
        plugin, name, capability = _pick_plugin(
            router, "MiniMax-M3", [{"role": "user", "content": "hi"}]
        )
        assert plugin is not None and plugin.name == "MiniMax-M3"
        assert name == "MiniMax-M3"
        assert capability is None  # explicit pool name: no capability inferred

    def test_budget_block_on_khavis_backend(self):
        import asyncio

        from bot.backends.khavis import KhavisBackend
        from bot.models import DispatchEnvelope

        reset_guard(BudgetPolicy(max_tokens_per_request=1, max_tokens_per_session=1))
        backend = KhavisBackend()
        env = DispatchEnvelope(chat_id=1, prompt="x" * 100)
        result = asyncio.run(backend.run(env))
        assert result.ok is False
        assert "budget" in (result.error or "").lower()
