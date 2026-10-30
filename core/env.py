"""Centralised environment loading for khavis.

Loads variables from the project-root ``.env`` file exactly once per
process, with explicit process-env precedence (existing values are kept;
``.env`` only fills gaps). Idempotent — safe to call from any entry point.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_loaded: bool = False


def _project_root() -> Path:
    # core/ is at <project>/core/, so the repo/install root is one level up.
    return Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    """Directory holding ``pools.yaml`` / ``capabilities.yaml``.

    Resolution order:

    1. ``KHAVIS_CONFIG_DIR`` (explicit override),
    2. ``./config`` under the current working directory,
    3. the package root's ``config/`` (editable installs, Docker at ``/app``).
    """
    import os

    override = os.environ.get("KHAVIS_CONFIG_DIR")
    if override:
        return Path(override)
    cwd_cfg = Path.cwd() / "config"
    if cwd_cfg.is_dir():
        return cwd_cfg
    return _project_root() / "config"


def find_env_file() -> Path | None:
    """Locate the ``.env`` file: cwd first, then the package root."""
    for candidate in (Path.cwd() / ".env", _project_root() / ".env"):
        if candidate.exists():
            return candidate
    return None


def load_env(file_path: Path | str | None = None, *, override: bool = False) -> bool:
    """Load ``.env`` into ``os.environ``.

    Returns ``True`` if a ``.env`` file was found and loaded, ``False``
    otherwise. Idempotent: once loaded (or once we have confirmed there is no
    file to load) every later call in this process is a no-op. Pass
    ``override=True`` to let ``.env`` win over variables already present in
    the real environment.
    """
    global _loaded
    if _loaded:
        return False
    path = Path(file_path) if file_path else find_env_file() or (Path.cwd() / ".env")
    if not path.exists():
        _loaded = True  # mark loaded even when missing so we don't keep looking
        return False
    load_dotenv(dotenv_path=path, override=override)
    _loaded = True
    return True


# NOTE: deliberately NOT calling ``load_env()`` here. Auto-loading on import
# would pollute test environments (tests that pass ``api_key=None`` to a
# plugin would silently inherit the real key from ``.env``). Callers that
# need .env (start.sh, integration_test.py, ...) must invoke
# ``load_env()`` explicitly.
