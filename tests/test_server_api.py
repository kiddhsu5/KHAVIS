"""HTTP-level tests for ``server/openai_api.py`` (auth, validation, SSE errors).

The router stack and plugin resolution are stubbed so nothing touches the
network; the request path (auth → content-type → validation → budget →
failover → response shaping) runs for real.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="khavis-serve needs the 'server' extra")

from fastapi.testclient import TestClient  # noqa: E402

import server.openai_api as api  # noqa: E402
from core.budget import reset_guard  # noqa: E402
from core.health import HealthPolicy, reset_tracker  # noqa: E402


def _plugin(
    name: str = "PoolA",
    *,
    result: dict[str, Any] | None = None,
    boom: Exception | None = None,
    provider_id: str = "openai_compat",
) -> MagicMock:
    p = MagicMock()
    p.name = name
    p.model = "upstream-model"
    p.provider_id = provider_id
    p.list_models.return_value = ["upstream-model"]
    if boom is not None:
        p.chat.side_effect = boom
    else:
        p.chat.return_value = result or {
            "choices": [
                {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "length"}
            ],
            "usage": {"prompt_tokens": None, "completion_tokens": 3, "total_tokens": None},
        }
    return p


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    reset_tracker(HealthPolicy(failure_threshold=1))
    reset_guard()
    monkeypatch.delenv("KHAVIS_API_KEY", raising=False)
    monkeypatch.setenv("KHAVIS_FAILOVER", "0")
    monkeypatch.setenv("KHAVIS_AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("KHAVIS_RAG_BACKEND", "none")
    yield
    reset_guard()


def _client(monkeypatch, plugin: MagicMock, *, key: str | None = None) -> TestClient:
    if key is not None:
        monkeypatch.setenv("KHAVIS_API_KEY", key)
    reg = MagicMock()
    reg.all.return_value = [plugin]
    reg.__iter__.return_value = iter([])
    monkeypatch.setattr(api, "_router_stack", lambda: (reg, MagicMock()))
    monkeypatch.setattr(
        api,
        "_pick_plugin",
        lambda router, model, messages, session_id=None: (plugin, plugin.name, None),
    )
    return TestClient(api.create_app())


BODY = {"model": "my-alias", "messages": [{"role": "user", "content": "hello"}]}


class TestAuth:
    def test_no_key_configured_allows_requests(self, monkeypatch):
        with _client(monkeypatch, _plugin()) as c:
            assert c.post("/v1/chat/completions", json=BODY).status_code == 200

    def test_key_required_when_configured(self, monkeypatch):
        with _client(monkeypatch, _plugin(), key="k-123") as c:
            r = c.post("/v1/chat/completions", json=BODY)
            assert r.status_code == 401
            assert r.headers.get("www-authenticate") == "Bearer"
            r = c.post("/v1/chat/completions", json=BODY, headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401
            assert c.get("/v1/models").status_code == 401
            ok = {"Authorization": "Bearer k-123"}
            assert c.post("/v1/chat/completions", json=BODY, headers=ok).status_code == 200
            assert c.get("/v1/models", headers=ok).status_code == 200


class TestBindSafety:
    def test_loopback_without_key_is_fine(self):
        api.check_bind_safety("127.0.0.1", "")
        api.check_bind_safety("::1", "")
        api.check_bind_safety("localhost", "")

    def test_public_bind_without_key_refused(self, monkeypatch):
        monkeypatch.delenv("KHAVIS_ALLOW_NO_AUTH", raising=False)
        with pytest.raises(SystemExit, match="KHAVIS_API_KEY"):
            api.check_bind_safety("0.0.0.0", "")

    def test_public_bind_with_key_ok(self):
        api.check_bind_safety("0.0.0.0", "secret")

    def test_explicit_opt_out_only_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("KHAVIS_ALLOW_NO_AUTH", "1")
        api.check_bind_safety("0.0.0.0", "")
        assert "KHAVIS_ALLOW_NO_AUTH" in caplog.text


class TestValidation:
    def test_non_json_content_type_is_415(self, monkeypatch):
        with _client(monkeypatch, _plugin()) as c:
            r = c.post(
                "/v1/chat/completions",
                content=json.dumps(BODY),
                headers={"Content-Type": "text/plain"},
            )
        assert r.status_code == 415

    def test_json_with_charset_is_accepted(self, monkeypatch):
        with _client(monkeypatch, _plugin()) as c:
            r = c.post(
                "/v1/chat/completions",
                content=json.dumps(BODY),
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        assert r.status_code == 200

    def test_malformed_json_is_400(self, monkeypatch):
        with _client(monkeypatch, _plugin()) as c:
            r = c.post(
                "/v1/chat/completions",
                content=b"{not json",
                headers={"Content-Type": "application/json"},
            )
        assert r.status_code == 400

    @pytest.mark.parametrize(
        "extra",
        [{"max_tokens": "lots"}, {"max_tokens": -1}, {"rag_k": "x"}, {"rag_k": 0}],
    )
    def test_bad_ints_are_400(self, monkeypatch, extra):
        with _client(monkeypatch, _plugin()) as c:
            r = c.post("/v1/chat/completions", json={**BODY, **extra})
        assert r.status_code == 400


class TestResponseShape:
    def test_model_usage_finish_reason_and_forwarded_params(self, monkeypatch):
        plug = _plugin()
        with _client(monkeypatch, plug) as c:
            r = c.post(
                "/v1/chat/completions",
                json={**BODY, "temperature": 0.2, "top_p": 0.9, "stop": ["\n"], "max_tokens": "64"},
            )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["model"] == "my-alias"  # echoes the requested model
        assert data["pool"] == "PoolA"
        assert data["choices"][0]["finish_reason"] == "length"
        assert data["usage"] == {"prompt_tokens": 0, "completion_tokens": 3, "total_tokens": 3}
        kwargs = plug.chat.call_args.kwargs
        assert kwargs["max_tokens"] == 64
        assert kwargs["temperature"] == 0.2
        assert kwargs["top_p"] == 0.9
        assert kwargs["stop"] == ["\n"]

    def test_model_falls_back_to_pool_name(self, monkeypatch):
        with _client(monkeypatch, _plugin()) as c:
            r = c.post("/v1/chat/completions", json={"messages": BODY["messages"]})
        assert r.json()["model"] == "PoolA"


class TestAdaptKwargs:
    def test_anthropic_style_translates_stop(self):
        p = MagicMock(provider_id="claude")
        out = api._adapt_kwargs(p, {"stop": "END", "top_p": 0.5, "seed": 1, "max_tokens": 9})
        assert out == {"stop_sequences": ["END"], "top_p": 0.5, "max_tokens": 9}

    def test_ollama_nests_options(self):
        p = MagicMock(provider_id="ollama")
        out = api._adapt_kwargs(p, {"temperature": 0.1, "stop": ["x"], "max_tokens": 9})
        assert out == {"max_tokens": 9, "options": {"temperature": 0.1, "stop": ["x"]}}

    def test_openai_compatible_passthrough(self):
        p = MagicMock(provider_id="openai_compat")
        kw = {"temperature": 0.1, "presence_penalty": 1.0}
        assert api._adapt_kwargs(p, kw) == kw


class TestStreaming:
    def _events(self, r) -> list[str]:
        return [ln[len("data: ") :] for ln in r.text.splitlines() if ln.startswith("data: ")]

    def test_stream_success(self, monkeypatch):
        with _client(monkeypatch, _plugin()) as c:
            r = c.post("/v1/chat/completions", json={**BODY, "stream": True})
        events = self._events(r)
        assert events[-1] == "[DONE]"
        chunk = json.loads(events[0])
        assert chunk["model"] == "my-alias"
        assert chunk["choices"][0]["delta"]["content"] == "hi"
        assert chunk["choices"][0]["finish_reason"] == "length"

    def test_stream_upstream_error_is_reported_in_band(self, monkeypatch):
        with _client(monkeypatch, _plugin(boom=RuntimeError("HTTP 500 upstream"))) as c:
            r = c.post("/v1/chat/completions", json={**BODY, "stream": True})
        assert r.status_code == 200
        events = self._events(r)
        assert events[-1] == "[DONE]"
        err = json.loads(events[0])["error"]
        assert "HTTP 500 upstream" in err["message"]
        assert err["code"] == 502


class TestRouterStack:
    def test_missing_config_logs_warning(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("KHAVIS_CONFIG_DIR", str(tmp_path))
        import logging

        with caplog.at_level(logging.WARNING, logger="khavis.server"):
            api._router_stack()
        assert "pools.yaml" in caplog.text
        assert "capabilities.yaml" in caplog.text

    def test_broken_capabilities_logs_warning(self, monkeypatch, tmp_path, caplog):
        (tmp_path / "pools.yaml").write_text("pools: []\n", encoding="utf-8")
        (tmp_path / "capabilities.yaml").write_text("capabilities: [", encoding="utf-8")
        monkeypatch.setenv("KHAVIS_CONFIG_DIR", str(tmp_path))
        import logging

        with caplog.at_level(logging.WARNING, logger="khavis.server"):
            api._router_stack()
        assert "failed to load" in caplog.text


class TestSessionAndReservation:
    def test_router_and_budget_share_the_header_session(self, monkeypatch):
        seen: dict[str, Any] = {}
        plugin = _plugin()

        def pick(router, model, messages, session_id=None):
            seen["pick"] = session_id
            return plugin, plugin.name, None

        with _client(monkeypatch, plugin) as c:
            monkeypatch.setattr(api, "_pick_plugin", pick)
            r = c.post("/v1/chat/completions", json=BODY, headers={"x-khavis-session": "s-42"})
        assert r.status_code == 200
        assert seen["pick"] == "s-42"
        from core.budget import get_guard

        assert "s-42" in get_guard()._sessions

    def test_session_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("KHAVIS_SESSION", "env-sess")
        assert api._session_id(None) == "env-sess"

    def test_upstream_error_releases_reservation(self, monkeypatch):
        from core.budget import get_guard

        with _client(monkeypatch, _plugin(boom=RuntimeError("boom"))) as c:
            r = c.post("/v1/chat/completions", json=BODY, headers={"x-khavis-session": "s-err"})
        assert r.status_code == 502
        assert not list(get_guard()._sessions.get("s-err") or [])

    def test_stream_include_usage_emits_usage_chunk(self, monkeypatch):
        with _client(monkeypatch, _plugin()) as c:
            r = c.post(
                "/v1/chat/completions",
                json={**BODY, "stream": True, "stream_options": {"include_usage": True}},
            )
        events = [
            line[len("data: ") :] for line in r.text.splitlines() if line.startswith("data: ")
        ]
        assert events[-1] == "[DONE]"
        usage_chunk = json.loads(events[-2])
        assert usage_chunk["choices"] == []
        assert usage_chunk["usage"]["completion_tokens"] == 3


# ----------------------------------------------------------------------
# khavis serve must load .env itself
# ----------------------------------------------------------------------
class TestServeLoadsDotenv:
    """Regression, found by running the documented entry point.

    ``scripts/start.sh`` called ``load_env()`` but nothing else did — including
    ``_router_stack()``, which is what ``khavis serve`` uses. Since
    ``pools.yaml`` resolves ``env_key`` via ``os.environ``, the server came up
    with no credentials and answered every request 401/502. The same bug was
    already fixed in ``doctor`` and ``chat``; this was the entry point that had
    been missed.
    """

    def test_router_stack_populates_pools_from_dotenv(self, tmp_path, monkeypatch):
        import os

        from core.env import _project_root as real_root
        from core.env import find_env_file
        from server.openai_api import _router_stack

        monkeypatch.setattr("core.env.config_dir", lambda: tmp_path)
        monkeypatch.setattr("core.env.find_env_file", lambda: tmp_path / ".env", raising=False)
        # Keep the real project root so discover() still finds providers/;
        # only the config dir is redirected.
        monkeypatch.setattr("core.env._project_root", lambda: real_root())
        (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")

        # Start from a shell with nothing set, which is the real-world case.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr("core.env._loaded", False, raising=False)

        reg, _router = _router_stack()
        plugin = reg.get("OpenAI-API")
        assert plugin is not None
        assert plugin.api_key == "sk-from-dotenv", (
            "khavis serve must load .env before resolving pools.yaml env_key"
        )
        # 透過模組屬性呼叫（即上方 monkeypatch 的版本），避免依賴 checkout 根目錄
        # 是否存在 .env——公開倉庫不會、也不應該附帶 .env。
        from core import env as _env

        assert _env.find_env_file() is not None
        assert "OPENAI_API_KEY" in os.environ
