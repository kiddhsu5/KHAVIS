"""Tests for the JudgeBackend's pick logic.

Uses a fake Ollama endpoint (no real HTTP) to validate parse + fallback
semantics. The actual ollama_chat call is overridden via dependency
injection.
"""

from __future__ import annotations

from bot.backends.judge import JudgeBackend, _parse_judge_pick
from bot.models import BackendResult, DispatchEnvelope, DispatchReport


class TestParseJudgePick:
    def test_plain_json(self):
        idx, reason = _parse_judge_pick('{"best": 2, "reason": "most detailed"}', 3)
        assert idx == 2
        assert reason == "most detailed"

    def test_code_fenced_json(self):
        raw = '```json\n{"best": 0, "reason": "only one"}\n```'
        idx, reason = _parse_judge_pick(raw, 1)
        assert idx == 0
        assert reason == "only one"

    def test_with_prose_around_json(self):
        raw = 'Sure thing! {"best": 1, "reason": "concise"} hope that helps'
        idx, reason = _parse_judge_pick(raw, 3)
        assert idx == 1
        assert reason == "concise"

    def test_out_of_range_index(self):
        idx, reason = _parse_judge_pick('{"best": 7, "reason": "x"}', 3)
        assert idx is None
        assert reason == ""

    def test_garbage(self):
        assert _parse_judge_pick("not json at all", 3) == (None, "")
        assert _parse_judge_pick("", 3) == (None, "")
        assert _parse_judge_pick("{}", 3) == (None, "")  # no "best" key


class TestJudgeBackendPick:
    def _report(self) -> DispatchReport:
        return DispatchReport(
            envelope=DispatchEnvelope(chat_id=1, prompt="Write hello world"),
            results=[
                BackendResult(
                    backend="claude",
                    ok=True,
                    text="Hello.",
                    model="m1",
                    latency_ms=100,
                ),
                BackendResult(
                    backend="codex",
                    ok=True,
                    text="Hello, world! (longer)",
                    model="m2",
                    latency_ms=200,
                ),
                BackendResult(
                    backend="khavis",
                    ok=False,
                    error="401",
                    model="m3",
                    latency_ms=50,
                ),
            ],
            consensus="",
        )

    def test_pick_first_when_only_one_succeeds(self, monkeypatch):
        report = DispatchReport(
            envelope=DispatchEnvelope(chat_id=1, prompt="x"),
            results=[
                BackendResult(backend="claude", ok=True, text="only", model="m"),
                BackendResult(backend="codex", ok=False, error="x"),
            ],
        )
        jb = JudgeBackend()
        # Skip the ollama HTTP call entirely by short-circuiting at judge_pick.
        out = asyncio_run(jb.judge_pick(report.envelope, report))
        assert out.ok
        assert out.text == "only"
        assert out.extra["picked_from"] == "claude"

    def test_picks_judges_choice(self, monkeypatch):
        """Patch the Ollama call so we deterministically pick index 1."""

        def fake_chat(self, user_msg: str) -> str:
            return '{"best": 1, "reason": "codex was more thorough"}'

        monkeypatch.setattr(JudgeBackend, "_ollama_chat", fake_chat)

        report = self._report()
        jb = JudgeBackend()
        out = asyncio_run(jb.judge_pick(report.envelope, report))
        assert out.ok
        assert "Hello, world! (longer)" in out.text
        assert out.extra["picked_from"] == "codex"
        assert out.model  # was set

    def test_falls_back_on_bad_json(self, monkeypatch):
        def bad_json(self, user_msg: str) -> str:
            return "definitely not json"

        monkeypatch.setattr(JudgeBackend, "_ollama_chat", bad_json)

        report = self._report()
        jb = JudgeBackend()
        out = asyncio_run(jb.judge_pick(report.envelope, report))
        # ok=False because judge itself failed; report.consensus will
        # fall back to longest-wins (the second entry is longer).
        assert out.ok is False
        assert "fell back" in out.error
        # The longest successful candidate is "Hello, world! (longer)".
        # The judge surfaces that as the fallback text.
        assert "Hello, world! (longer)" in out.text

    def test_falls_back_on_index_out_of_range(self, monkeypatch):
        def oob(self, user_msg: str) -> str:
            return '{"best": 99, "reason": "x"}'

        monkeypatch.setattr(JudgeBackend, "_ollama_chat", oob)

        report = self._report()
        jb = JudgeBackend()
        out = asyncio_run(jb.judge_pick(report.envelope, report))
        assert out.ok is False
        assert "fell back" in out.error

    def test_no_successful_candidates(self, monkeypatch):
        # If the judge returns something but all backends failed, we
        # shouldn't even attempt to call ollama.
        def should_not_be_called(self, user_msg: str) -> str:
            raise AssertionError("ollama should not be called")

        monkeypatch.setattr(JudgeBackend, "_ollama_chat", should_not_be_called)

        report = DispatchReport(
            envelope=DispatchEnvelope(chat_id=1, prompt="x"),
            results=[
                BackendResult(backend="claude", ok=False, error="boom", model="m"),
                BackendResult(backend="codex", ok=False, error="kaboom", model="m"),
            ],
        )
        jb = JudgeBackend()
        out = asyncio_run(jb.judge_pick(report.envelope, report))
        assert out.ok is False
        assert "no successful" in out.error


# ---------- async test helper ----------
def asyncio_run(coro):
    """Run a coroutine in a fresh event loop for synchronous tests."""
    import asyncio

    return asyncio.run(coro)
