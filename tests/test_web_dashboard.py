"""Tests for Phase 4 runner, history, and dashboard renderers."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot import history as bot_history
from bot.phases import PHASES, run_phase
from web.dashboard import api_payload, render_dashboard_html, render_wall_html


class TestHistory:
    def test_record_and_tail(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("KHAVIS_LOG_DIR", str(tmp_path))
        bot_history.record({"kind": "dispatch", "ok": True, "prompt": "hello"})
        bot_history.record({"kind": "phase", "phase": "plan", "ok": False, "prompt": "x"})
        rows = bot_history.tail(10)
        assert len(rows) == 2
        assert rows[0]["kind"] == "phase"  # newest first
        assert rows[1]["prompt"] == "hello"

    def test_truncates_long_fields(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("KHAVIS_LOG_DIR", str(tmp_path))
        bot_history.record(
            {
                "prompt": "A" * 5000,
                "consensus": "B" * 5000,
                "result": "C" * 5000,
                "review_details": "D" * 5000,
                "error": "E" * 5000,
            }
        )
        row = bot_history.tail(1)[0]
        for key in ("prompt", "consensus", "result", "review_details", "error"):
            assert len(row[key]) == 2001
            assert row[key].endswith("…")

    def test_tail_missing_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("KHAVIS_LOG_DIR", str(tmp_path / "nope"))
        assert bot_history.tail() == []


class TestPhases:
    def test_phase_names(self):
        names = [n for n, _ in PHASES]
        assert "plan" in names and "code" in names
        assert "pipeline" not in names  # virtual stage in run_phase()

    def test_unknown_phase(self):
        r = asyncio.run(run_phase("nope", "hi"))
        assert r.ok is False
        assert "unknown phase" in r.error

    def test_plan_phase_uses_run_team(self, monkeypatch):
        calls = {}

        def fake_run_team(task, session_id="default"):
            calls["task"] = task
            return {"plan": "do the thing", "error": ""}

        import agents

        monkeypatch.setattr(agents, "run_team", fake_run_team)
        r = asyncio.run(run_phase("plan", "write a parser", on_progress=None))
        assert r.ok is True
        assert "do the thing" in r.output
        assert "[phase:plan]" in calls["task"]


class TestDashboard:
    def test_api_payload_shape(self):
        p = api_payload(pools=[{"name": "X", "ok": True}], bot={"ok": True}, history=[])
        assert "pools" in p and "wall" in p and "history" in p
        assert "quotas" in p and "alerts" in p

    def test_render_dashboard_contains_wall_and_pools(self):
        html = render_dashboard_html(
            pools=[
                {
                    "name": "MiMo-Code",
                    "provider_id": "mimo",
                    "ok": True,
                    "models": ["mimo-v2.6-pro"],
                    "capabilities": ["code"],
                }
            ],
            bot={"ok": True, "detail": "active"},
            history=[{"kind": "dispatch", "ok": True, "prompt": "hi", "latency_ms": 10, "ts": 0}],
        )
        assert "MiMo-Code" in html
        assert "個人開發者 wall" in html
        assert "dashboard" in html
        assert "即時模型配額與重置倒數" in html
        assert "countdown" in html
        assert "LangGraph" in html

    def test_render_wall(self):
        html = render_wall_html(
            [{"label": "alice", "detail": "homelab", "url": "https://example.com"}]
        )
        assert "alice" in html and "homelab" in html


class TestPublicRedaction:
    """The dashboard is public: no prompts, chat ids or per-session budget rows."""

    SECRET_PROMPT = "my private prompt with sk-live-thing"
    CHAT_ID = 987654321

    def _history(self):
        return [
            {
                "kind": "dispatch",
                "ok": True,
                "prompt": self.SECRET_PROMPT,
                "chat_id": self.CHAT_ID,
                "backends": ["claude", "codex"],
                "consensus_source": "judge",
                "ts": 0,
            },
            {
                "kind": "phase",
                "phase": "plan",
                "ok": False,
                "error": "Traceback ... token=abc",
                "prompt": self.SECRET_PROMPT,
                "chat_id": self.CHAT_ID,
                "latency_ms": 12,
            },
        ]

    def _guard_with_session(self):
        from core.budget import reset_guard

        g = reset_guard()
        g.record(session_id=f"chat-{self.CHAT_ID}", pool="PoolA", usage={"prompt_tokens": 10})
        g.record(session_id="chat-111", pool="PoolA", usage={"completion_tokens": 5})
        return g

    def test_public_history_whitelist(self):
        from web.dashboard import public_history

        out = public_history(self._history())
        blob = repr(out)
        assert self.SECRET_PROMPT not in blob
        assert str(self.CHAT_ID) not in blob
        assert "token=abc" not in blob
        assert out[0]["backends"] == ["claude", "codex"]
        assert out[1]["phase"] == "plan" and out[1]["latency_ms"] == 12

    def test_api_payload_budget_is_aggregate_only(self):
        import json

        from core.budget import reset_guard

        self._guard_with_session()
        try:
            p = api_payload(pools=[], bot={"ok": True}, history=self._history())
        finally:
            reset_guard()
        blob = json.dumps(p)
        assert self.SECRET_PROMPT not in blob
        assert str(self.CHAT_ID) not in blob
        assert "chat-111" not in blob
        sessions = p["budget"]["sessions"]
        assert sessions["count"] == 2
        assert sessions["tokens"] == 15
        assert p["budget"]["by_pool"]["PoolA"]["tokens"] == 15

    def test_render_dashboard_hides_prompt_and_chat_id(self):
        from core.budget import reset_guard

        self._guard_with_session()
        try:
            html = render_dashboard_html(pools=[], bot={"ok": True}, history=self._history())
        finally:
            reset_guard()
        assert self.SECRET_PROMPT not in html
        assert str(self.CHAT_ID) not in html
        assert "claude, codex" in html
