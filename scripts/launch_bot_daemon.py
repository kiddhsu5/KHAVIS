#!/usr/bin/env python3
"""Daemonize the K.H.A.V.I.S. Telegram dispatch bot.

Launches the bot via a POSIX double-fork so it survives the launching
shell's exit. Mirrors what ``nohup`` does on macOS without depending on
``setsid`` (which doesn't ship by default).

The resulting child writes its PID to ``--pid-file`` and its logs to
``--log-file``. Both default to a private per-user directory
(``$XDG_CACHE_HOME/khavis-bot`` or ``~/.cache/khavis-bot``, mode 0700;
files mode 0600, opened with ``O_NOFOLLOW``) — not world-writable
``/tmp``, where another local user could pre-plant a symlink or read the
log. A second invocation stops the existing daemon before re-launching —
but only after confirming that PID really is the bot — so it is safe to
re-run.

Usage:
    python scripts/launch_bot_daemon.py                    # use defaults
    python scripts/launch_bot_daemon.py --help
    python scripts/launch_bot_daemon.py --stop             # stop + exit

After launch:
    tail -f ~/.cache/khavis-bot/bot.log
    python scripts/launch_bot_daemon.py --stop             # tear down
"""

from __future__ import annotations

import argparse
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _default_python(project_root: Path = DEFAULT_PROJECT_ROOT) -> str:
    """Prefer the project's ``.venv`` interpreter, else whoever runs us."""
    venv_python = project_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _default_state_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "khavis-bot"


DEFAULT_PYTHON = _default_python()
DEFAULT_STATE_DIR = _default_state_dir()
DEFAULT_PID_FILE = DEFAULT_STATE_DIR / "bot.pid"
DEFAULT_LOG_FILE = DEFAULT_STATE_DIR / "bot.log"

# Substring that must appear in a PID's command line before we signal it.
BOT_CMDLINE_MARKER = "bot.main"
_STOP_GRACE_S = 10.0


# ---------------------------------------------------------------------------
# Private files
# ---------------------------------------------------------------------------
def ensure_private_dir(path: Path) -> None:
    """Create ``path`` (0700) and refuse symlinks / dirs owned by someone else."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise SystemExit(f"[launcher] refusing to use {path}: not a real directory")
    if st.st_uid != os.getuid():
        raise SystemExit(f"[launcher] refusing to use {path}: owned by uid {st.st_uid}")
    if stat.S_IMODE(st.st_mode) & 0o077:
        os.chmod(path, 0o700)


def open_private(path: Path, flags: int) -> int:
    """``os.open`` with mode 0600 + ``O_NOFOLLOW`` (a planted symlink fails)."""
    fd = os.open(path, flags | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)  # tighten a file left over from an older run
    except OSError:
        os.close(fd)
        raise
    return fd


def write_pid_file(pid_file: Path, pid: int) -> None:
    fd = open_private(pid_file, os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"{pid}\n")


def read_pid_file(pid_file: Path) -> int | None:
    try:
        fd = os.open(pid_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    with os.fdopen(fd) as fh:
        try:
            return int(fh.read().strip())
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# .env parsing
# ---------------------------------------------------------------------------
def _strip_inline_comment(value: str) -> str:
    """Drop `` # comment`` from an unquoted value (``#`` needs whitespace before it)."""
    for i, ch in enumerate(value):
        if ch == "#" and (i == 0 or value[i - 1].isspace()):
            return value[:i].rstrip()
    return value


def _parse_env_fallback(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ("'", '"') and value[0] in value[1:]:
            # Quoted value, possibly followed by an inline comment: KEY="v"  # note
            value = value[1 : value.index(value[0], 1)]
        else:
            value = _strip_inline_comment(value)
        out[key.strip()] = value
    return out


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse ``.env`` like python-dotenv: quotes stripped, inline comments dropped.

    Uses ``dotenv.dotenv_values`` when available, with a small fallback
    parser so the launcher still works under a bare interpreter.
    """
    try:
        from dotenv import dotenv_values  # noqa: PLC0415
    except ImportError:
        return _parse_env_fallback(path)
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


# ---------------------------------------------------------------------------
# Process control
# ---------------------------------------------------------------------------
_PROCFS = Path("/proc/self/cmdline")


def _cmdline(pid: int) -> str | None:
    """Command line of ``pid``, distinguishing three outcomes:

    * the command line text — the process is alive and we could read it;
    * ``""`` — the process is gone (or genuinely has an empty command line);
    * ``None`` — *this platform gives us no way to look it up*: macOS and the
      BSDs ship no procfs, and when ``ps`` is also unavailable (sandboxed
      shells, minimal containers) there is nothing left to fall back on.

    The ``None`` case matters: it means "cannot check", which is not the same
    as "checked, and it is not the bot". Conflating the two would make the
    daemon impossible to stop while claiming we had verified it was someone
    else's process.
    """
    if _PROCFS.exists():
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return ""  # procfs is there, so this pid is simply not in it
        return raw.replace(b"\0", b" ").decode(errors="replace").strip()
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_bot_process(pid: int) -> bool:
    """True only if ``pid`` is alive *and* its command line is the bot.

    Fails safe: when the command line cannot be read at all we return False
    and never signal. Callers that need to tell "not the bot" apart from
    "could not check" should call :func:`_cmdline` themselves.
    """
    if pid <= 1 or not _alive(pid):
        return False
    cmd = _cmdline(pid)
    return cmd is not None and BOT_CMDLINE_MARKER in cmd


def terminate(pid: int, grace_s: float = _STOP_GRACE_S) -> bool:
    """SIGTERM, wait up to ``grace_s``, then SIGKILL. Returns True if it exited."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    time.sleep(0.2)
    return not _alive(pid)


def _stop_recorded(pid_file: Path, *, verbose: bool) -> None:
    """Stop the daemon recorded in ``pid_file`` (if it really is the bot)."""
    if not pid_file.exists():
        if verbose:
            print("[launcher] no pid file; nothing to stop")
        return
    pid = read_pid_file(pid_file)
    if pid is None:
        pid_file.unlink(missing_ok=True)
        return
    if not _alive(pid):
        if verbose:
            print(f"[launcher] pid {pid} not running")
    elif not is_bot_process(pid):
        # PID was recycled by an unrelated process — never signal it.
        cmd = _cmdline(pid)
        if cmd is None:
            print(
                f"[launcher] pid {pid} is alive but its command line cannot be read on this"
                f" system (no /proc, and 'ps' is unavailable); refusing to signal it",
                file=sys.stderr,
            )
        else:
            print(
                f"[launcher] pid {pid} is not the bot ({cmd!r}); leaving it alone",
                file=sys.stderr,
            )
    elif terminate(pid):
        print(f"[launcher] stopped bot pid {pid}", file=sys.stderr)
    else:
        print(f"[launcher] WARNING: pid {pid} did not exit", file=sys.stderr)
    pid_file.unlink(missing_ok=True)


def _first_fork() -> None:
    if os.fork() > 0:
        os._exit(0)


def _second_fork() -> None:
    if os.fork() > 0:
        os._exit(0)


def launch(
    *,
    project_root: Path,
    python_bin: str,
    pid_file: Path,
    log_file: Path,
    mode: str,
) -> Path:
    """Daemonise the bot. Returns the PID of the daemon process."""
    ensure_private_dir(pid_file.parent)
    ensure_private_dir(log_file.parent)

    _stop_recorded(pid_file, verbose=False)

    # Parse .env *before* forking so a missing/broken file is reported on
    # the operator's terminal instead of only in the daemon log.
    env_file = project_root / ".env"
    if not env_file.exists():
        raise SystemExit(f"[launcher] {env_file} not found; refusing to start the bot")
    env_values = parse_env_file(env_file)

    # Round 1: detach from controlling tty.
    _first_fork()
    os.setsid()
    # Round 2: ensure the daemon can never reacquire a tty.
    _second_fork()

    # We are the daemon. Re-direct stdio to the (private) log file.
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull, "rb", 0) as devnull:
        os.dup2(devnull.fileno(), 0)
    fd = open_private(log_file, os.O_WRONLY | os.O_TRUNC)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)

    write_pid_file(pid_file, os.getpid())
    print(f"[launcher] bot daemon pid={os.getpid()}", flush=True)

    # Load the project .env into the daemon's environment.
    os.environ.update(env_values)

    os.chdir(str(project_root))
    os.execv(
        python_bin,
        [python_bin, "-B", "-u", "-m", "bot.main", "--mode", mode],
    )
    # execv does not return on success.
    raise RuntimeError("execv returned; daemon failed to start")  # pragma: no cover


def stop(pid_file: Path) -> None:
    _stop_recorded(pid_file, verbose=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project-root",
        type=Path,
        default=DEFAULT_PROJECT_ROOT,
        help="Path to the K.H.A.V.I.S. checkout (default: %(default)s)",
    )
    parser.add_argument(
        "--python",
        default=DEFAULT_PYTHON,
        help="Python interpreter to use (default: %(default)s)",
    )
    parser.add_argument(
        "--pid-file",
        type=Path,
        default=DEFAULT_PID_FILE,
        help="Where the daemon writes its PID (default: %(default)s)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help="Where the daemon writes stdout/stderr (default: %(default)s)",
    )
    parser.add_argument(
        "--mode",
        choices=("polling", "webhook"),
        default=os.environ.get("BOT_MODE", "polling"),
        help="Bot listen mode",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop the running daemon (SIGTERM, then SIGKILL) and exit",
    )
    args = parser.parse_args()
    if args.stop:
        stop(args.pid_file)
        return 0
    launch(
        project_root=args.project_root,
        python_bin=args.python,
        pid_file=args.pid_file,
        log_file=args.log_file,
        mode=args.mode,
    )
    return 0  # unreachable in practice; the parent already exited


if __name__ == "__main__":
    raise SystemExit(main())
