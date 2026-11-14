"""Tests for the enhanced collaborative CLI (`core/cli_ui.py`, `core/cli_run.py`, `khavis status`)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from bot import history as bot_history
from core import cli_run, cli_ui
from core.cli import main


@pytest.fixture
def isolated_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg_dir = tmp_path / ".khavis"
    monkeypatch.setattr(cli_ui, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cli_ui, "CONFIG_PATH", cfg_dir / "cli_config.json")
    monkeypatch.setattr(cli_ui, "HISTORY_PATH", cfg_dir / "cli_history")
    monkeypatch.setenv("KHAVIS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("NO_COLOR", "1")
    cli_ui.C.set_theme("mono")
    return tmp_path


def test_banner_and_chain_formatting(
    isolated_cli: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert "preempt" in cli_ui.VALID_MODES
    assert (
        cli_ui.format_agent_chain(["claude", "codex"], "relay", {"claude": "sonnet-4"})
        == "claude(sonnet-4) → codex"
    )
    assert cli_ui.format_agent_chain(["claude", "codex"], "parallel") == "claude + codex"

    cfg = cli_ui.load_config()
    cfg["agents"] = ["minimax", "claude"]
    cfg["mode"] = "preempt"
    cli_ui.print_banner(cfg, [{"id": "claude", "status": "ready"}], workspace=isolated_cli)
    out = capsys.readouterr().out
    assert "K.H.A.V.I.S." in out
    assert "minimax → claude" in out
    assert "preempt" in out
    assert "搶佔交接" in out


def test_settings_cancel_preserves_original_and_save_persists(
    isolated_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = cli_ui.load_config()
    assert cfg["agents"] == ["khavis"]

    # Mutate mode -> 2 (relay), then cancel with 0 -> original cfg must remain untouched
    inputs = iter(["2", "2", "0"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))
    res_cancelled = cli_ui.open_settings(cfg, available_ids=["claude", "codex", "khavis"])
    assert res_cancelled["mode"] == "auto_loop"
    assert not cli_ui.CONFIG_PATH.exists()

    # Reorder agents "2 1" -> codex, claude, set mode preempt ("3"), and save ("8")
    inputs_save = iter(["1", "2 1", "", "2", "3", "8"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(inputs_save))
    res_saved = cli_ui.open_settings(cfg, available_ids=["claude", "codex", "khavis"])
    assert res_saved["agents"] == ["codex", "claude"]
    assert res_saved["mode"] == "preempt"
    assert cli_ui.CONFIG_PATH.is_file()

    reloaded = cli_ui.load_config()
    assert reloaded["agents"] == ["codex", "claude"]
    assert reloaded["mode"] == "preempt"


def test_run_flag_override_history_sync_and_continue(
    isolated_cli: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Persist non-default config to verify --agents khavis --mode auto_loop CAN override it
    cli_ui.save_config(
        {
            **cli_ui.DEFAULT_CONFIG,
            "agents": ["claude", "codex"],
            "mode": "relay",
        }
    )

    calls: list[dict[str, Any]] = []

    async def _fake_dispatch(
        prompt: str,
        agents: list[str],
        mode: str = "auto_loop",
        shared_dir: Path | None = None,
        agent_models: dict[str, str] | None = None,
        previous_context: dict[str, Any] | None = None,
        lock_order: bool = True,
    ) -> dict[str, Any]:
        calls.append(
            {
                "prompt": prompt,
                "agents": list(agents),
                "mode": mode,
                "models": dict(agent_models or {}),
                "previous_context": previous_context,
            }
        )
        return {
            "collab_mode": mode,
            "consensus": f"完成：{prompt}",
            "consensus_source": agents[0],
            "results": [{"backend": agents[0], "ok": True, "text": f"完成：{prompt}"}],
            "collaboration_steps": [
                {
                    "stage": "Step 1",
                    "agent": agents[0],
                    "role": "主棒",
                    "ok": True,
                    "latency_ms": 120,
                }
            ],
            "shared_changed_files": [{"path": "docs/plan.md", "status": "A", "size": "2.1 KB"}],
            "next_actions": [{"label": "補測試", "prompt": "請補測試", "mode": "auto_loop"}],
            "latency_ms": 120,
        }

    monkeypatch.setattr(cli_run, "_run_dispatch", _fake_dispatch)

    # 1. Explicit override back to khavis + auto_loop + --model override
    rc1 = main(
        [
            "run",
            "第一輪任務",
            "--agents",
            "khavis",
            "--mode",
            "auto_loop",
            "--model",
            "khavis=MiniMax-M3",
            "--cwd",
            str(isolated_cli),
        ]
    )
    assert rc1 == 0
    out1 = capsys.readouterr().out
    assert "完成：第一輪任務" in out1
    assert "[A] docs/plan.md (2.1 KB)" in out1
    assert "{'path'" not in out1
    assert calls[-1]["agents"] == ["khavis"]
    assert calls[-1]["mode"] == "auto_loop"
    assert calls[-1]["models"]["khavis"] == "MiniMax-M3"

    # Verify history recorded with unified base36 thread id
    rows = bot_history.tail(5)
    assert len(rows) == 1
    tid = rows[0]["thread"]
    assert re.fullmatch(r"t[0-9a-z]+", tid)
    assert rows[0]["step"] == 1

    # 2. Second invocation with -c (--continue) uses saved config & continues thread step=2
    rc2 = main(["run", "第二輪追問", "-c", "--cwd", str(isolated_cli)])
    assert rc2 == 0
    capsys.readouterr()
    assert calls[-1]["agents"] == ["claude", "codex"]
    assert calls[-1]["mode"] == "relay"
    assert calls[-1]["previous_context"]["thread"] == tid
    assert calls[-1]["previous_context"]["step"] == 1

    rows2 = bot_history.tail(5)
    assert len(rows2) == 2
    assert rows2[0]["thread"] == tid
    assert rows2[0]["step"] == 2


def test_team_repl_slash_commands_and_followup(
    isolated_cli: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dispatched: list[tuple[str, list[str], str]] = []

    async def _fake_dispatch(
        prompt: str,
        agents: list[str],
        mode: str = "auto_loop",
        **_kw: Any,
    ) -> dict[str, Any]:
        dispatched.append((prompt, list(agents), mode))
        return {
            "collab_mode": mode,
            "consensus": "第一行\n第二行\n第三行",
            "consensus_source": agents[-1],
            "results": [{"backend": agents[-1], "ok": True, "text": "ok"}],
            "collaboration_steps": [
                {
                    "stage": "第 1 棒",
                    "agent": agents[0],
                    "role": "先鋒",
                    "ok": True,
                    "latency_ms": 80,
                }
            ],
            "critique_summary": "審查通過",
            "review_details": "細節良好",
            "shared_changed_files": [],
            "next_actions": [{"label": "自動補測試", "prompt": "請自動補測試", "mode": "critique"}],
            "latency_ms": 80,
        }

    monkeypatch.setattr(cli_run, "_run_dispatch", _fake_dispatch)

    cmds = iter(
        [
            "/order claude,codex",
            "/mode preempt",
            "/run 檢查系統",
            "/steps",
            "/history",
            "/1",
            "/quit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _p="": next(cmds))

    rc = main(["team", "--no-banner", "--cwd", str(isolated_cli)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "審查通過" in out
    assert "最近對話串" in out
    assert "再見！" in out
    assert dispatched[0] == ("檢查系統", ["claude", "codex"], "preempt")
    assert dispatched[1] == ("請自動補測試", ["claude", "codex"], "critique")


def test_status_and_agents_json(
    isolated_cli: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli_run,
        "load_custom_agents",
        lambda: [{"id": "my-bot", "name": "MyBot", "type": "http", "default_model": "glm-5"}],
    )
    assert main(["agents", "--json"]) == 0
    ag_data = json.loads(capsys.readouterr().out)
    ids = [r["id"] for r in ag_data]
    assert "claude" in ids
    assert "my-bot" in ids

    assert main(["status", "--json"]) == 0
    st_data = json.loads(capsys.readouterr().out)
    assert "workbench" in st_data
    assert "pools" in st_data
    assert any(a["id"] == "my-bot" for a in st_data["agents"])
