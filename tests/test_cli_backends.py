"""Unit tests for Tencent Cloud CLI backends (Antigravity, Claude, MiniMax, opencode).

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from bot.backends import (
    AntigravityBackend,
    ClaudeBackend,
    CodexBackend,
    MinimaxBackend,
    OpencodeBackend,
)
from bot.dispatch import DispatchRouter
from bot.models import DispatchEnvelope


def test_claude_default_model_is_juapi_gemini4(monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_DISPATCH_MODEL", raising=False)
    backend = ClaudeBackend()
    assert backend._model == "gemini-4-argon"


def test_claude_juapi_model_maps_to_anthropic_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("JUAPI_API_KEY", "juapi-test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    backend = ClaudeBackend(model="gemini4")
    envelope = DispatchEnvelope(chat_id=1, prompt="hello")
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b'{"result":"ok"}', b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        result = asyncio.run(backend.run(envelope))

    assert result.ok is True
    child_env = mock_exec.call_args.kwargs["env"]
    assert child_env["ANTHROPIC_API_KEY"] == "juapi-test-key"
    assert child_env["ANTHROPIC_AUTH_TOKEN"] == "juapi-test-key"
    assert child_env["ANTHROPIC_BASE_URL"] == "https://www.juapi.net"


def test_codex_default_model_is_juapi_kimi(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_DISPATCH_MODEL", raising=False)
    backend = CodexBackend()
    assert backend._model == "kimi-k3.1-pro"


def test_codex_juapi_model_maps_to_openai_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("JUAPI_API_KEY", "juapi-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    backend = CodexBackend(model="kimi-k3.1-pro")
    envelope = DispatchEnvelope(chat_id=1, prompt="hello")
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b'{"text":"ok"}', b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        result = asyncio.run(backend.run(envelope))

    assert result.ok is True
    child_env = mock_exec.call_args.kwargs["env"]
    assert child_env["OPENAI_API_KEY"] == "juapi-test-key"
    assert child_env["OPENAI_BASE_URL"] == "https://www.juapi.net/v1"


def test_antigravity_backend_health_not_found() -> None:
    backend = AntigravityBackend(binary="__non_existent_antigravity_cli__")
    health = asyncio.run(backend.health())
    assert health.ok is False
    assert "not in PATH" in health.detail


def test_minimax_backend_health_not_found() -> None:
    backend = MinimaxBackend(binary="__non_existent_minimax_cli__")
    health = asyncio.run(backend.health())
    assert health.ok is False
    assert "not in PATH" in health.detail


def test_opencode_backend_health_not_found() -> None:
    backend = OpencodeBackend(binary="__non_existent_opencode_cli__")
    health = asyncio.run(backend.health())
    assert health.ok is False
    assert "not in PATH" in health.detail


def test_cli_backends_mock_run() -> None:
    envelope = DispatchEnvelope(chat_id=1, prompt="Implement quicksort in Python")

    # 1. Test Antigravity mock run
    agy = AntigravityBackend(binary="antigravity")
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b'{"text": "def quicksort(): pass"}', b"")
        proc.returncode = 0
        mock_exec.return_value = proc

        res = asyncio.run(agy.run(envelope))
        assert res.ok is True
        assert res.backend == "antigravity"
        assert "quicksort" in res.text

    # 2. Test MiniMax mock run
    minimax = MinimaxBackend(binary="minimax")
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b'{"text": "MiniMax generated python code"}', b"")
        proc.returncode = 0
        mock_exec.return_value = proc

        res = asyncio.run(minimax.run(envelope))
        assert res.ok is True
        assert res.backend == "minimax"
        assert "MiniMax" in res.text

    # 3. Test opencode mock run
    opencode = OpencodeBackend(binary="opencode")
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b'{"text": "GLM codegeex implementation"}', b"")
        proc.returncode = 0
        mock_exec.return_value = proc

        res = asyncio.run(opencode.run(envelope))
        assert res.ok is True
        assert res.backend == "opencode"
        assert "GLM" in res.text


def test_dispatch_router_with_all_backends() -> None:
    backends = {
        "antigravity": AntigravityBackend(binary="__non_existent__"),
        "minimax": MinimaxBackend(binary="__non_existent__"),
        "opencode": OpencodeBackend(binary="__non_existent__"),
    }
    router = DispatchRouter(backends)  # type: ignore[arg-type]

    envelope = DispatchEnvelope(chat_id=1, prompt="hello world", only=["antigravity", "minimax"])
    report = asyncio.run(router.dispatch(envelope))

    assert len(report.results) == 2
    b_names = [r.backend for r in report.results]
    assert "antigravity" in b_names
    assert "minimax" in b_names


def test_claude_effort_mode_is_passed_to_cli(monkeypatch) -> None:
    monkeypatch.setenv("JUAPI_API_KEY", "juapi-test-key")
    envelope = DispatchEnvelope(chat_id=1, prompt="hello", thinking_mode="high")
    backend = ClaudeBackend(model="JUAPI_API_KEY::gemini-4-argon")
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b'{"result":"ok"}', b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        result = asyncio.run(backend.run(envelope))

    assert result.ok is True
    args = list(mock_exec.call_args.args)
    assert "--effort" in args
    assert args[args.index("--effort") + 1] == "high"
    assert mock_exec.call_args.kwargs["env"]["KHAVIS_THINKING_MODE"] == "high"


def test_codex_reasoning_effort_is_passed_to_cli(monkeypatch) -> None:
    monkeypatch.setenv("JUAPI_API_KEY", "juapi-test-key")
    envelope = DispatchEnvelope(chat_id=1, prompt="hello", thinking_mode="xhigh")
    backend = CodexBackend(model="JUAPI_API_KEY::kimi-k3.1-pro")
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b'{"text":"ok"}', b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        result = asyncio.run(backend.run(envelope))

    assert result.ok is True
    args = list(mock_exec.call_args.args)
    assert "-c" in args
    assert "model_reasoning_effort=xhigh" in args


def test_auto_thinking_does_not_change_existing_cli_args() -> None:
    envelope = DispatchEnvelope(chat_id=1, prompt="hello")
    backend = ClaudeBackend(model="gemini4")
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b'{"result":"ok"}', b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        asyncio.run(backend.run(envelope))

    args = list(mock_exec.call_args.args)
    assert "--effort" not in args


def test_provider_qualified_model_ref_is_stripped_before_cli(monkeypatch) -> None:
    monkeypatch.setenv("JUAPI_API_KEY", "juapi-test-key")
    envelope = DispatchEnvelope(chat_id=1, prompt="hello")
    backend = ClaudeBackend(model="JUAPI_API_KEY::gemini-4-argon")
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b'{"result":"ok"}', b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        result = asyncio.run(backend.run(envelope))

    assert result.ok is True
    args = list(mock_exec.call_args.args)
    assert "gemini-4-argon" in args
    assert not any("::" in str(arg) for arg in args)
