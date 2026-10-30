"""Tests for ``scripts/launch_bot_daemon.py`` (private files, pid safety, .env parsing)."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import scripts.launch_bot_daemon as launcher

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX daemon launcher")


ENV_TEXT = """\
# comment line
BOT_TOKEN="123:abc"
WEBHOOK_SECRET='s3cret'   # inline comment after quotes
ALLOWED_CHAT_IDS=1,2   # inline comment
EMPTY=
HASH_IN_VALUE=abc#def
export EXPORTED=yes
"""

EXPECTED = {
    "BOT_TOKEN": "123:abc",
    "WEBHOOK_SECRET": "s3cret",
    "ALLOWED_CHAT_IDS": "1,2",
    "EMPTY": "",
    "HASH_IN_VALUE": "abc#def",
    "EXPORTED": "yes",
}


class TestEnvParsing:
    def test_dotenv_backed(self, tmp_path: Path):
        f = tmp_path / ".env"
        f.write_text(ENV_TEXT)
        assert launcher.parse_env_file(f) == EXPECTED

    def test_fallback_parser_matches(self, tmp_path: Path):
        f = tmp_path / ".env"
        f.write_text(ENV_TEXT)
        assert launcher._parse_env_fallback(f) == EXPECTED


class TestPrivateFiles:
    def test_dir_is_0700_and_files_0600(self, tmp_path: Path):
        d = tmp_path / "state"
        launcher.ensure_private_dir(d)
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
        pid_file = d / "bot.pid"
        launcher.write_pid_file(pid_file, 1234)
        assert stat.S_IMODE(pid_file.stat().st_mode) == 0o600
        assert launcher.read_pid_file(pid_file) == 1234

    def test_loose_existing_file_is_tightened(self, tmp_path: Path):
        f = tmp_path / "bot.log"
        f.write_text("old")
        f.chmod(0o644)
        os.close(launcher.open_private(f, os.O_WRONLY | os.O_TRUNC))
        assert stat.S_IMODE(f.stat().st_mode) == 0o600

    def test_symlinked_log_is_refused(self, tmp_path: Path):
        target = tmp_path / "victim"
        target.write_text("keep me")
        link = tmp_path / "bot.log"
        link.symlink_to(target)
        with pytest.raises(OSError):
            launcher.open_private(link, os.O_WRONLY | os.O_TRUNC)
        assert target.read_text() == "keep me"

    def test_symlinked_dir_is_refused(self, tmp_path: Path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        with pytest.raises(SystemExit):
            launcher.ensure_private_dir(link)

    def test_defaults_are_not_in_tmp(self):
        assert not str(launcher.DEFAULT_PID_FILE).startswith("/tmp/")
        assert not str(launcher.DEFAULT_LOG_FILE).startswith("/tmp/")

    def test_default_python_prefers_project_venv(self, tmp_path: Path):
        assert launcher._default_python(tmp_path) == sys.executable
        venv_py = tmp_path / ".venv" / "bin" / "python"
        venv_py.parent.mkdir(parents=True)
        venv_py.write_text("")
        assert launcher._default_python(tmp_path) == str(venv_py)


class TestStopSafety:
    def test_unrelated_pid_is_never_signalled(self, tmp_path: Path):
        # Our own (pytest) PID is alive but is not the bot.
        pid_file = tmp_path / "bot.pid"
        launcher.write_pid_file(pid_file, os.getpid())
        assert launcher.is_bot_process(os.getpid()) is False
        launcher.stop(pid_file)  # would kill pytest if the check were missing
        assert not pid_file.exists()

    def test_stale_pid_file_is_cleaned(self, tmp_path: Path):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        pid_file = tmp_path / "bot.pid"
        launcher.write_pid_file(pid_file, proc.pid)
        launcher.stop(pid_file)
        assert not pid_file.exists()

    def test_bot_process_gets_sigterm_then_sigkill(self, tmp_path, monkeypatch):
        # A fake "bot.main" that ignores SIGTERM, so escalation is exercised.
        script = tmp_path / "bot.main.py"
        script.write_text(
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n"
        )
        proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
        try:
            assert proc.stdout is not None and proc.stdout.readline().strip() == "ready"
            # Recognising the process needs a working /proc or ps; stub the
            # lookup so this test covers the SIGTERM->SIGKILL escalation only.
            # The real lookup has its own test below.
            monkeypatch.setattr(
                launcher, "_cmdline", lambda pid: f"python -m {launcher.BOT_CMDLINE_MARKER}"
            )
            assert launcher.is_bot_process(proc.pid) is True
            t0 = time.monotonic()
            # (Our child lingers as a zombie until reaped, so the return value
            # is not meaningful here — check how the process actually died.)
            launcher.terminate(proc.pid, grace_s=0.5)
            proc.wait(timeout=5)
            assert time.monotonic() - t0 < 5
            assert proc.returncode == -9
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_real_cmdline_lookup_recognises_a_bot_process(self, tmp_path):
        """The actual /proc-or-ps lookup, not a stub, must spot a real bot.main."""
        script = tmp_path / "bot.main.py"
        script.write_text("import time\ntime.sleep(30)\n")
        proc = subprocess.Popen([sys.executable, str(script)])
        try:
            if launcher._cmdline(proc.pid) is None:
                pytest.skip("no /proc and no usable `ps` on this system")
            assert launcher.is_bot_process(proc.pid) is True
        finally:
            proc.kill()
            proc.wait()

    def test_unreadable_cmdline_says_cannot_check_not_wrong_process(
        self, tmp_path, monkeypatch, capsys
    ):
        """ "Cannot read the cmdline" must not be reported as "this is someone else".

        Reporting the unverifiable case as a verified mismatch would leave the
        operator with a daemon they can never stop, and a message blaming a
        process we never actually looked at.
        """
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            monkeypatch.setattr(launcher, "_cmdline", lambda pid: None)
            pid_file = tmp_path / "bot.pid"
            launcher.write_pid_file(pid_file, proc.pid)
            launcher.stop(pid_file)
            err = capsys.readouterr().err
            assert "cannot be read" in err
            assert "is not the bot" not in err
            assert proc.poll() is None, "an unverifiable pid must be left running"
        finally:
            proc.kill()
            proc.wait()
