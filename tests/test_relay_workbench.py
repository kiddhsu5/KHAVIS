"""Regression tests for the relay workbench, ordered handoff, thread merging, and navigation."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.backends.base import Backend, HealthResult
from bot.dispatch import DispatchRouter
from bot.models import BackendResult, DispatchEnvelope
from core.collab_orchestrator import run_collaborative_workflow
from web.landing import render_landing_html
from web.relay_workbench import render_relay_workbench_html
from web.workbench_c import render_workbench_html_c


class _StubBackend(Backend):
    def __init__(
        self, name: str, text: str = "", ok: bool = True, call_log: list[str] | None = None
    ) -> None:
        self.name = name  # type: ignore[assignment]
        self.model = f"{name}-model"
        self._text = text or f"result from {name}"
        self._ok = ok
        self._call_log = call_log

    async def run(self, envelope: DispatchEnvelope) -> BackendResult:
        if self._call_log is not None:
            self._call_log.append(str(self.name))
        return BackendResult(
            backend=self.name,  # type: ignore[arg-type]
            ok=self._ok,
            text=self._text if self._ok else "",
            error="" if self._ok else f"{self.name} quota exhausted",
            latency_ms=12,
            model=self.model,
        )

    async def health(self) -> HealthResult:
        return HealthResult(ok=self._ok, detail="ok")


def test_relay_workbench_exposes_user_controls() -> None:
    html = render_relay_workbench_html()
    assert "額度交接台" in html
    assert 'id="workflows"' in html
    assert 'id="agent-picks"' in html
    assert 'id="holder-select"' in html
    assert 'id="next-select"' in html
    assert 'id="btn-swap-holders"' in html
    assert 'id="btn-lock-order"' in html
    assert "data-model-agent" in html
    assert "/api/workbench/dispatch" in html
    assert "不顯示假百分比" in html
    assert "X-KHAVIS-Console-Token" in html


def test_navigation_links_across_all_four_entries() -> None:
    relay_html = render_relay_workbench_html()
    c_html = render_workbench_html_c()
    landing_html = render_landing_html([], {"ok": True})

    for page_html in (relay_html, c_html, landing_html):
        assert "/workbench" in page_html
        assert "/workbench-c" in page_html
        assert "/agents" in page_html
        assert "/database" in page_html

    # Verify /agents provider & model picker controls are intact
    assert "c-provider-form-box" in c_html
    assert "cOpenAgentModelPicker" in c_html
    assert "/api/workbench/setup" in c_html
    assert "/api/workbench/provider_models" in c_html
    assert "/api/workbench/agent_models" in c_html


def test_thread_ui_has_continue_from_here_and_thread_persistence() -> None:
    relay_html = render_relay_workbench_html()
    c_html = render_workbench_html_c()

    assert "從此接續" in relay_html
    assert "從此接續" in c_html
    assert "continueFromTurn" in relay_html
    assert "cContinue(" in c_html
    assert "C_LEGACY_THREAD_WINDOW_SEC = 1800" in c_html
    assert "LEGACY_THREAD_WINDOW_SEC = 1800" in relay_html


def test_relay_workflow_respects_agent_order(tmp_path: Path) -> None:
    call_log: list[str] = []
    backends = {
        "claude": _StubBackend("claude", "claude step", ok=True, call_log=call_log),
        "codex": _StubBackend("codex", "codex step", ok=True, call_log=call_log),
        "minimax": _StubBackend("minimax", "minimax step", ok=True, call_log=call_log),
    }
    router = DispatchRouter(backends)  # type: ignore[arg-type]
    cwds = {k: tmp_path for k in backends}

    res = asyncio.run(
        run_collaborative_workflow(
            router=router,
            backends=backends,
            agents=["claude", "codex", "minimax"],
            agent_order=["minimax", "codex", "claude"],
            lock_order=True,
            prompt="Run ordered relay",
            capability=None,
            shared_dir=tmp_path,
            agent_cwds=cwds,
            collab_mode="relay",
        )
    )

    assert res["ok"] is True
    assert call_log == ["minimax", "codex", "claude"]
    assert [r["backend"] for r in res["results"]] == ["minimax", "codex", "claude"]
    assert res["agent_order"] == ["minimax", "codex", "claude"]
    assert res["lock_order"] is True


def test_preempt_workflow_fails_over_in_agent_order(tmp_path: Path) -> None:
    call_log: list[str] = []
    backends = {
        "claude": _StubBackend("claude", "", ok=False, call_log=call_log),
        "codex": _StubBackend("codex", "codex took over cleanly", ok=True, call_log=call_log),
        "minimax": _StubBackend("minimax", "minimax backup", ok=True, call_log=call_log),
    }
    router = DispatchRouter(backends)  # type: ignore[arg-type]
    cwds = {k: tmp_path for k in backends}

    res = asyncio.run(
        run_collaborative_workflow(
            router=router,
            backends=backends,
            agents=["claude", "codex", "minimax"],
            agent_order=["claude", "codex", "minimax"],
            lock_order=True,
            prompt="Test failover handoff",
            capability=None,
            shared_dir=tmp_path,
            agent_cwds=cwds,
            collab_mode="preempt",
        )
    )

    assert res["ok"] is True
    assert call_log == ["claude", "codex"]
    assert [r["backend"] for r in res["results"]] == ["claude", "codex"]
    assert "codex took over cleanly" in res["consensus"]
    assert "claude(✗) → codex(✓)" in res["consensus_source"]


def test_consecutive_dispatches_share_base36_thread_and_increment_step(
    tmp_path: Path, monkeypatch
) -> None:
    import io
    import json
    import re
    from unittest.mock import AsyncMock, patch

    from bot import history as bot_history
    from bot.models import DispatchReport
    from scripts.status_server import Handler

    monkeypatch.setenv("KHAVIS_LOG_DIR", str(tmp_path))

    class _DummyHandler(Handler):
        def __init__(self, path: str, body: bytes) -> None:
            self.path = path
            self.command = "POST"
            self.headers = {"Content-Length": str(len(body)), "Host": "localhost:8080"}  # type: ignore[assignment]
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 55555)
            self.responses: list[tuple[int, bytes, str]] = []

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.responses.append((code, body, ctype))

    async def _fake_dispatch(env: DispatchEnvelope) -> DispatchReport:
        only = list(env.only or ["minimax"])
        results = [
            BackendResult(
                backend=b,  # type: ignore[arg-type]
                ok=True,
                text=f"output from {b}",
                latency_ms=10,
                model=f"{b}-model",
            )
            for b in only
        ]
        return DispatchReport(
            envelope=env,
            results=results,
            consensus=results[-1].text,
            consensus_source=only[-1],
        )

    with patch("bot.dispatch.DispatchRouter.dispatch", new_callable=AsyncMock) as mock_disp:
        mock_disp.side_effect = _fake_dispatch

        # Turn 1: No previous_context -> backend generates base36 thread id, step=1
        body1 = json.dumps(
            {
                "prompt": "First turn prompt",
                "agents": ["claude", "minimax"],
                "order": ["minimax", "claude"],
                "lockOrder": True,
                "workflow": "relay",
                "collab_mode": "relay",
            }
        ).encode("utf-8")
        h1 = _DummyHandler("/api/workbench/dispatch", body1)
        h1.do_POST()
        assert h1.responses[0][0] == 200
        d1 = json.loads(h1.responses[0][1].decode("utf-8"))
        assert d1["ok"] is True
        tid = d1["thread"]
        assert re.fullmatch(r"t[0-9a-z]+", tid), f"Expected base36 thread id, got {tid}"
        assert d1["step"] == 1
        assert d1["agent_order"] == ["minimax", "claude"]
        assert d1["lock_order"] is True
        assert [r["backend"] for r in d1["results"]] == ["minimax", "claude"]

        # Turn 2: Carries previous_context={thread, step} -> same thread, step=2
        body2 = json.dumps(
            {
                "prompt": "Second turn follow-up",
                "agents": ["minimax", "claude"],
                "order": ["minimax", "claude"],
                "lockOrder": True,
                "collab_mode": "relay",
                "previous_context": {
                    "thread": tid,
                    "step": d1["step"],
                    "prompt": "First turn prompt",
                    "consensus": d1["consensus"],
                },
            }
        ).encode("utf-8")
        h2 = _DummyHandler("/api/workbench/dispatch", body2)
        h2.do_POST()
        assert h2.responses[0][0] == 200
        d2 = json.loads(h2.responses[0][1].decode("utf-8"))
        assert d2["ok"] is True
        assert d2["thread"] == tid
        assert d2["step"] == 2

    recorded = bot_history.tail(10)
    assert len(recorded) == 2
    assert recorded[0]["thread"] == tid
    assert recorded[1]["thread"] == tid
    assert sorted(r["step"] for r in recorded) == [1, 2]


def test_history_thread_scrollable_and_not_truncated(monkeypatch, tmp_path: Path):
    """Historical thread views in /workbench and /workbench-c must be scrollable and preserve long outputs."""
    from bot import history as bot_history

    relay_html = (
        Path(__file__).resolve().parent.parent / "web" / "relay_workbench.html"
    ).read_text(encoding="utf-8")
    assert "grid-template-rows: minmax(0, 1fr);" in relay_html
    assert ".main { min-width: 0; min-height: 0; height: 100%; overflow: hidden;" in relay_html
    assert ".scroll { flex: 1; min-height: 0; overflow-y: auto;" in relay_html

    from web.workbench_c import render_workbench_html_c

    c_html = render_workbench_html_c()
    assert "#c-main{min-width:0;min-height:0;flex:1 1 0%;overflow-y:auto;" in c_html
    assert "max-height:30rem;overflow-y:auto" not in c_html
    assert "t.consensus || t.raw || t.result" in c_html
    assert "t.review_details || t.review" in c_html

    monkeypatch.setenv("KHAVIS_LOG_DIR", str(tmp_path))
    long_consensus = "長篇交付成果內容_" * 500  # 4500 chars (> 2000)
    bot_history.record(
        {
            "kind": "workbench",
            "prompt": "測試長篇歷史成果",
            "consensus": long_consensus,
            "review_details": "審查細節_" * 400,
        }
    )
    row = bot_history.tail(1)[0]
    assert row["consensus"] == long_consensus
    assert len(row["review_details"]) == 2000


def test_project_diff_button_removed_from_workbench_ui():
    """專案檔案 Diff buttons should not appear in conversation turn or task card action bars."""
    from web.workbench_c import render_workbench_html_c

    c_html = render_workbench_html_c()
    assert ">專案檔案 Diff</button>" not in c_html
    assert 'onclick="cShowDiff()"' not in c_html
