"""Tests for core.audit (request audit log) and core.cli (khavis console script).

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core import audit, cli


# ---------------------------------------------------------------------------
# core.audit
# ---------------------------------------------------------------------------
class TestAuditPath:
    def test_default_path_under_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KHAVIS_AUDIT_FILE", raising=False)
        path = audit.audit_log_path()
        assert path is not None
        assert path.name == "audit.jsonl"
        assert path.parent.name == "config"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "custom.jsonl"
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", str(target))
        assert audit.audit_log_path() == target

    @pytest.mark.parametrize("value", ["", "   ", "/dev/null"])
    def test_disabled(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", value)
        assert audit.audit_log_path() is None


class TestLogRequest:
    def _read_records(self, path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_writes_record(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "audit.jsonl"
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", str(target))
        audit.log_request(
            capability="general",
            pool="Ollama-Mac",
            model="llama3.2",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            cost_usd=0.00123456789,
            latency_ms=842,
            status="ok",
            session_id="chat-1",
        )
        recs = self._read_records(target)
        assert len(recs) == 1
        rec = recs[0]
        assert rec["capability"] == "general"
        assert rec["pool"] == "Ollama-Mac"
        assert rec["model"] == "llama3.2"
        assert rec["prompt_tokens"] == 10
        assert rec["completion_tokens"] == 5
        assert rec["total_tokens"] == 15
        assert rec["cost_usd"] == pytest.approx(0.001235, abs=1e-6)
        assert rec["latency_ms"] == 842
        assert rec["status"] == "ok"
        assert rec["session_id"] == "chat-1"
        assert rec["ts"].endswith("Z")

    def test_error_record_carries_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = tmp_path / "audit.jsonl"
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", str(target))
        audit.log_request(pool="X", status="error", error="boom")
        rec = self._read_records(target)[0]
        assert rec["status"] == "error"
        assert rec["error"] == "boom"

    def test_disabled_writes_nothing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "audit.jsonl"
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", "/dev/null")
        audit.log_request(pool="X")
        assert not target.exists()

    def test_unwritable_path_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", "/proc/self/mem/audit.jsonl")
        audit.log_request(pool="X")  # must not raise

    def test_appends_across_calls(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "audit.jsonl"
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", str(target))
        audit.log_request(pool="A")
        audit.log_request(pool="B")
        assert [r["pool"] for r in self._read_records(target)] == ["A", "B"]

    def test_bad_usage_never_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "audit.jsonl"
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", str(target))
        audit.log_request(
            pool="X",
            usage={"prompt_tokens": "lots", "completion_tokens": object()},
            cost_usd="free",  # type: ignore[arg-type]
            latency_ms="slow",  # type: ignore[arg-type]
        )
        rec = self._read_records(target)[0]
        assert rec["prompt_tokens"] == 0 and rec["completion_tokens"] == 0
        assert rec["cost_usd"] == 0.0 and rec["latency_ms"] == 0

    def test_usage_not_a_dict_never_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", str(tmp_path / "a.jsonl"))
        audit.log_request(pool="X", usage=["not", "a", "dict"])  # type: ignore[arg-type]

    def test_error_is_redacted(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "audit.jsonl"
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", str(target))
        audit.log_request(
            pool="X",
            status="error",
            error=(
                "AuthenticationError: Incorrect API key provided: sk-proj-ABCDEF1234567890 "
                "url=https://g.example/v1?key=AIzaSyZZZZZZZZZZZZZZZZ&alt=json "
                "headers={'Authorization': 'Bearer abcdef0123456789xyz'} api_key=hunter2hunter2"
            ),
        )
        err = self._read_records(target)[0]["error"]
        for secret in ("ABCDEF1234567890", "AIzaSyZZZZ", "abcdef0123456789xyz", "hunter2hunter2"):
            assert secret not in err
        assert "AuthenticationError" in err

    def test_default_path_respects_config_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("KHAVIS_AUDIT_FILE", raising=False)
        monkeypatch.setenv("KHAVIS_CONFIG_DIR", str(tmp_path))
        assert audit.audit_log_path() == tmp_path / "audit.jsonl"

    def test_rotation(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "audit.jsonl"
        monkeypatch.setenv("KHAVIS_AUDIT_FILE", str(target))
        monkeypatch.setenv("KHAVIS_AUDIT_MAX_BYTES", "200")
        for i in range(10):
            audit.log_request(pool=f"P{i}")
        rotated = tmp_path / "audit.jsonl.1"
        assert rotated.exists()
        assert target.stat().st_size < 400
        assert self._read_records(target)[-1]["pool"] == "P9"


class TestRedact:
    @pytest.mark.parametrize(
        ("raw", "secret"),
        [
            ("key sk-ant-api03-abcdefghijklmnop", "abcdefghijklmnop"),
            ("Bearer eyJhbGciOiJIUzI1NiJ9.payload", "eyJhbGciOiJIUzI1NiJ9"),
            ("?key=AIzaSyA-1234567890_abcdef", "1234567890_abcdef"),
            ("x-api-key: plain-secret-value", "plain-secret-value"),
            ("nvapi-ABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOP"),
        ],
    )
    def test_masks_secrets(self, raw: str, secret: str) -> None:
        assert secret not in audit.redact_secrets(raw)

    def test_leaves_normal_text(self) -> None:
        msg = "ConnectionResetError: peer closed connection (task=sk)"
        assert audit.redact_secrets(msg) == msg
        assert audit.redact_secrets(None) == ""


# ---------------------------------------------------------------------------
# core.cli
# ---------------------------------------------------------------------------
class TestCli:
    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            cli.main(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert f"khavis {cli.VERSION}" in out

    def test_init_writes_starter_config(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = cli.main(["init", "--config-dir", str(tmp_path)])
        assert rc == 0
        for name in ("pools.yaml", "capabilities.yaml", "agents.yaml.example"):
            assert (tmp_path / name).exists(), name
        assert "wrote" in capsys.readouterr().out

    def test_init_never_overwrites(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "pools.yaml").write_text("pools: []\n", encoding="utf-8")
        cli.main(["init", "--config-dir", str(tmp_path)])
        assert (tmp_path / "pools.yaml").read_text(encoding="utf-8") == "pools: []\n"
        assert "kept existing" in capsys.readouterr().out

    def test_doctor_reports_pools(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("KHAVIS_AUDIT_FILE", raising=False)
        rc = cli.main(["doctor"])
        out = capsys.readouterr().out
        assert "python" in out
        assert "pools.yaml" in out
        # Doctor exits 0 when config + discovery succeed in the repo.
        assert rc == 0

    def test_bare_invocation_shows_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.main([])
        assert rc == 0
        assert "usage" in capsys.readouterr().out.lower()

    def test_module_entry_runs(self) -> None:
        # ``python -m core.cli --version`` must exit cleanly.
        import subprocess

        proc = subprocess.run(
            [sys.executable, "-m", "core.cli", "--version"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0
        assert "khavis" in proc.stdout


class _FakePlug:
    def __init__(self, name: str, *, ok: bool = True, raises: bool = False) -> None:
        self.name = name
        self._ok = ok
        self._raises = raises

    def health_check(self) -> dict:
        if self._raises:
            raise RuntimeError("probe exploded")
        return {"ok": self._ok, "detail": "" if self._ok else "down"}

    def check_quota(self) -> dict:
        return {"remaining": 5, "total": 10, "tier": "t"}


class _FakeRegistry:
    plugs: list = []
    applied: list = []

    def __init__(self, _root=None) -> None:
        pass

    def discover(self):
        return self

    def apply_pools_config(self, path):
        type(self).applied.append(Path(path))
        return self

    def errors(self) -> list:
        return []

    def __iter__(self):
        return iter(type(self).plugs)

    def __len__(self) -> int:
        return len(type(self).plugs)


class TestCliHealth:
    @pytest.fixture(autouse=True)
    def _fake(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        import core.registry as registry_mod
        from core.health import HealthPolicy, reset_tracker

        _FakeRegistry.plugs = [
            _FakePlug("Good"),
            _FakePlug("Down", ok=False),
            _FakePlug("Boom", raises=True),
        ]
        _FakeRegistry.applied = []
        monkeypatch.setattr(registry_mod, "PluginRegistry", _FakeRegistry)
        monkeypatch.setenv("KHAVIS_CONFIG_DIR", str(tmp_path))
        reset_tracker(HealthPolicy(failure_threshold=1))
        yield
        reset_tracker()

    def test_health_refreshes_by_default_and_uses_config_dir(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = cli.main(["health"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "probing 3 pool(s)" in out
        assert _FakeRegistry.applied == [tmp_path / "pools.yaml"]
        assert "Good" in out and "closed" in out
        assert "Boom" in out and "open" in out
        assert "khavis health --reset" not in out
        assert "per-process" in out

    def test_health_no_refresh_shows_cold_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.main(["health", "--no-refresh"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "probing" not in out
        assert "unknown" in out

    def test_doctor_survives_raising_health_check(self, capsys: pytest.CaptureFixture[str]) -> None:
        """One plugin's exploding probe must not abort the whole report.

        The pool rows are now grouped ("+ Good" for ready, a summary otherwise)
        rather than one line each, so this asserts the behaviour that matters —
        the surviving pool is still reported and the failure is not hidden —
        instead of the exact rendering.
        """
        cli.main(["doctor"])
        out = capsys.readouterr().out
        assert "discovered 3 pools" in out
        assert "+ Good" in out, "a healthy pool must still be reported"
        assert "Boom" in out and "probe exploded" in out, "the explosion must stay visible"
        assert "plugin discovery" not in out, "the report must not abort"
