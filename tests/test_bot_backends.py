"""Unit tests for the subprocess backend wrappers (arg building + JSON parsing)."""

from __future__ import annotations

import asyncio
import json

import pytest

from bot.backends.claude import _parse_claude_json
from bot.models import DispatchEnvelope


class TestParseClaudeJson:
    def test_real_claude_code_json_shape(self):
        """``claude -p --output-format json`` puts the answer in ``result``.

        Regression guard: the parser used to look only for ``text`` /
        ``content`` / ``output`` and so returned an empty string for every
        real Claude Code response.
        """
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "usage": {"input_tokens": 3352, "output_tokens": 2455},
            "modelUsage": {"mimo-v2.6-pro": {"contextWindow": 200000}},
        }
        text, model, tok_in, tok_out = _parse_claude_json(json.dumps(payload), "hi")
        assert text == "OK"
        assert model == "mimo-v2.6-pro"
        assert tok_in == 3352
        assert tok_out == 2455

    def test_model_field_wins_over_model_usage(self):
        payload = {"result": "x", "model": "claude-opus-5", "modelUsage": {"other": {}}}
        _, model, _, _ = _parse_claude_json(json.dumps(payload), "")
        assert model == "claude-opus-5"

    def test_text_fallback_keys_still_work(self):
        assert _parse_claude_json(json.dumps({"text": "a"}), "")[0] == "a"
        assert _parse_claude_json(json.dumps({"content": "b"}), "")[0] == "b"
        assert _parse_claude_json(json.dumps({"output": "c"}), "")[0] == "c"

    def test_non_json_falls_back_to_raw(self):
        text, model, tok_in, tok_out = _parse_claude_json("just plain text", "")
        assert text == "just plain text"
        assert model == ""
        assert (tok_in, tok_out) == (0, 0)

    def test_empty_input(self):
        assert _parse_claude_json("", "p") == ("", "", 0, 0)
        assert _parse_claude_json("   ", "p") == ("", "", 0, 0)

    def test_usage_alias_keys(self):
        payload = {"result": "x", "usage": {"input": 7, "output": 9}}
        _, _, tok_in, tok_out = _parse_claude_json(json.dumps(payload), "")
        assert (tok_in, tok_out) == (7, 9)


class TestParseClaudeJsonArray:
    def test_event_array_uses_final_result(self):
        events = [
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {}},
            {"type": "result", "result": "OK", "usage": {"input_tokens": 1, "output_tokens": 2}},
        ]
        text, _model, tok_in, tok_out = _parse_claude_json(json.dumps(events), "")
        assert text == "OK"
        assert (tok_in, tok_out) == (1, 2)


# ---------------------------------------------------------------------------
# Subprocess plumbing (fake asyncio.create_subprocess_exec)
# ---------------------------------------------------------------------------
class _FakeProc:
    def __init__(self, stdout=b"", stderr=b"", rc=0, hang=False):
        self._out, self._err, self.returncode = stdout, stderr, rc
        self._hang = hang
        self.killed = False
        self.waited = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)
        return self._out, self._err

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return -9


def _patch_exec(monkeypatch, proc=None, exc=None):
    calls: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if exc is not None:
            raise exc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


ENV = DispatchEnvelope(chat_id=1, prompt="--help me")


class TestClaudeSubprocess:
    def test_prompt_follows_double_dash(self, monkeypatch):
        from bot.backends.claude import ClaudeBackend

        calls = _patch_exec(monkeypatch, _FakeProc(stdout=b'{"result": "hi"}'))
        r = asyncio.run(ClaudeBackend().run(ENV))
        assert r.ok and r.text == "hi"
        args = list(calls[0])
        assert args[-2:] == ["--", "--help me"]

    def test_timeout_kills_and_reaps(self, monkeypatch):
        from bot.backends.claude import ClaudeBackend

        proc = _FakeProc(hang=True)
        _patch_exec(monkeypatch, proc)
        r = asyncio.run(ClaudeBackend(timeout_s=0.01).run(ENV))  # type: ignore[arg-type]
        assert r.ok is False and "timeout" in r.error
        assert proc.killed and proc.waited


class TestCodexSubprocess:
    def test_prompt_follows_double_dash(self, monkeypatch):
        from bot.backends.codex import CodexBackend

        calls = _patch_exec(monkeypatch, _FakeProc(stdout=b'{"text": "yo"}'))
        r = asyncio.run(CodexBackend().run(ENV))
        assert r.ok and r.text == "yo"
        assert list(calls[0])[-2:] == ["--", "--help me"]

    def test_unexpected_exception_becomes_failed_result(self, monkeypatch):
        from bot.backends.codex import CodexBackend

        _patch_exec(monkeypatch, exc=PermissionError("denied"))
        r = asyncio.run(CodexBackend().run(ENV))
        assert r.ok is False and r.backend == "codex"
        assert "denied" in r.error

    def test_run_plain_is_protected(self, monkeypatch):
        from bot.backends.codex import CodexBackend

        _patch_exec(monkeypatch, exc=OSError("exec format error"))
        r = asyncio.run(CodexBackend()._run_plain(ENV))
        assert r.ok is False and "exec format error" in r.error

    def test_run_plain_uses_double_dash(self, monkeypatch):
        from bot.backends.codex import CodexBackend

        calls = _patch_exec(monkeypatch, _FakeProc(stdout=b"plain"))
        r = asyncio.run(CodexBackend()._run_plain(ENV))
        assert r.ok and r.text == "plain"
        assert list(calls[0])[-2:] == ["--", "--help me"]
        assert "--json" not in calls[0]

    def test_timeout_kills_and_reaps(self, monkeypatch):
        from bot.backends.codex import CodexBackend

        proc = _FakeProc(hang=True)
        _patch_exec(monkeypatch, proc)
        r = asyncio.run(CodexBackend(timeout_s=0.01).run(ENV))  # type: ignore[arg-type]
        assert "timeout" in r.error
        assert proc.killed and proc.waited

    def test_health_decodes_invalid_utf8(self, monkeypatch):
        from bot.backends import codex as codex_mod

        monkeypatch.setattr(codex_mod.shutil, "which", lambda b: "/usr/bin/codex")
        _patch_exec(monkeypatch, _FakeProc(stdout=b"codex \xff\xfe 1.0\n"))
        h = asyncio.run(codex_mod.CodexBackend().health())
        assert h.ok is True and "1.0" in h.detail


class TestKhavisBackend:
    @pytest.fixture(autouse=True)
    def _guard(self):
        from core.budget import reset_guard

        reset_guard()
        yield
        reset_guard()

    def test_attribution_records_none(self, monkeypatch):
        import sys
        import types

        from bot.backends.khavis import KhavisBackend

        fake = types.ModuleType("agents")
        fake.run_team = lambda prompt, session_id=None: {  # type: ignore[attr-defined]
            "final": "answer",
            "attribution": {"records": None},
        }
        monkeypatch.setitem(sys.modules, "agents", fake)
        r = asyncio.run(KhavisBackend().run(DispatchEnvelope(chat_id=1, prompt="p")))
        assert r.ok is True, r.error
        assert r.extra["steps"] == "0"


class TestJudgeOffloadsBlockingCall:
    def test_ollama_chat_runs_in_worker_thread(self, monkeypatch):
        import threading

        from bot.backends.judge import JudgeBackend
        from bot.models import BackendResult, DispatchReport

        main_thread = threading.get_ident()
        seen: list[int] = []

        def fake_chat(self, user_msg):
            seen.append(threading.get_ident())
            return '{"best": 0, "reason": "r"}'

        monkeypatch.setattr(JudgeBackend, "_ollama_chat", fake_chat)
        env = DispatchEnvelope(chat_id=1, prompt="p")
        report = DispatchReport(
            envelope=env,
            results=[
                BackendResult(backend="claude", ok=True, text="a" * 30),
                BackendResult(backend="codex", ok=True, text="b" * 30),
            ],
        )
        out = asyncio.run(JudgeBackend().judge_pick(env, report))
        assert out.ok
        assert seen and seen[0] != main_thread
