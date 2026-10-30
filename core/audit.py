"""Per-request audit log (``config/audit.jsonl``).

Every chat request served through the HTTP API (and any other caller that
opts in) is appended as one JSON line carrying: timestamp, capability,
pool, model, token counts, estimated cost, latency and outcome status —
the record shape documented in ``docs/FAQ.md`` and ``docs/CONFIGURATION.md``.

Path resolution order:

1. ``KHAVIS_AUDIT_FILE`` env var (explicit override)
2. ``audit.jsonl`` under :func:`core.env.config_dir` (which itself honours
   ``KHAVIS_CONFIG_DIR``)

Set ``KHAVIS_AUDIT_FILE=/dev/null`` (or an empty string) to disable
audit logging entirely. Logging failures never break a request: the
writer swallows I/O and conversion errors so a full disk (or a malformed
usage dict) cannot take down the router.

Error strings are passed through :func:`redact_secrets` before they are
written, because upstream SDK exceptions routinely echo the request URL or
headers — and with them the API key.

The file is rotated once it exceeds ``KHAVIS_AUDIT_MAX_BYTES`` (default
10 MiB; ``0`` disables rotation): the current file is renamed to
``audit.jsonl.1`` (replacing any previous one) and a fresh file is started.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

_DEFAULT_MAX_BYTES = 10 * 1024 * 1024

# Patterns that look like credentials. Order matters: the more specific
# ``key=value`` / ``Bearer`` forms run before the bare-token forms so the
# label survives ("api_key=***" reads better than "***").
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ***"),
    (
        re.compile(
            r"(?i)\b(x-api-key|api[_-]?key|access[_-]?token|client[_-]?secret|secret|password)"
            r"(\s*[=:]\s*[\"']?)[^\s\"'&,;)]{6,}"
        ),
        r"\1\2***",
    ),
    # Bare ``key=`` / ``token=`` only in query-string form (``?key=AIza...``).
    (re.compile(r"(?i)\b(key|token)=[^\s\"'&,;)]{6,}"), r"\1=***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "sk-***"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{10,}"), "AIza***"),
    (re.compile(r"\bnvapi-[A-Za-z0-9_-]{8,}"), "nvapi-***"),
    (re.compile(r"\bgsk_[A-Za-z0-9]{8,}"), "gsk_***"),
    (re.compile(r"\bxai-[A-Za-z0-9]{8,}"), "xai-***"),
    (re.compile(r"\bhf_[A-Za-z0-9]{8,}"), "hf_***"),
)


def redact_secrets(text: str | None) -> str:
    """Mask substrings that look like API keys / bearer tokens.

    Covers ``sk-...`` (OpenAI / Anthropic / DeepSeek style), ``AIza...``
    (Google), a few other common vendor prefixes, ``Bearer <token>`` and
    ``api_key=`` / ``key=`` / ``token=`` query or header parameters.
    Never raises; ``None`` becomes ``""``.
    """
    if not text:
        return ""
    out = str(text)
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def audit_log_path() -> Path | None:
    """Resolve the audit log path, or ``None`` when logging is disabled."""
    override = os.getenv("KHAVIS_AUDIT_FILE")
    if override is not None:
        if override.strip() == "" or override.strip() == "/dev/null":
            return None
        return Path(override).expanduser()
    from .env import config_dir  # noqa: PLC0415 - keep import light

    return config_dir() / "audit.jsonl"


def _max_bytes() -> int:
    raw = os.getenv("KHAVIS_AUDIT_MAX_BYTES")
    if raw is None or raw.strip() == "":
        return _DEFAULT_MAX_BYTES
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_MAX_BYTES


def _rotate_if_needed(path: Path) -> None:
    limit = _max_bytes()
    if limit <= 0:
        return
    try:
        if path.stat().st_size < limit:
            return
    except OSError:
        return
    os.replace(path, path.with_name(path.name + ".1"))


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def log_request(
    *,
    capability: str | None = None,
    pool: str | None = None,
    model: str | None = None,
    usage: dict[str, Any] | None = None,
    cost_usd: float | None = None,
    latency_ms: int | None = None,
    status: str = "ok",
    session_id: str | None = None,
    error: str | None = None,
) -> None:
    """Append one audit record. Best-effort — never raises."""
    try:
        path = audit_log_path()
        if path is None:
            return

        usage = usage if isinstance(usage, dict) else {}
        prompt_tokens = _as_int(usage.get("prompt_tokens"))
        completion_tokens = _as_int(usage.get("completion_tokens"))
        total_tokens = _as_int(usage.get("total_tokens")) or (prompt_tokens + completion_tokens)
        try:
            cost = round(float(cost_usd or 0.0), 6)
        except (TypeError, ValueError):
            cost = 0.0

        record: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
            "capability": capability,
            "pool": pool,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost,
            "latency_ms": _as_int(latency_ms),
            "status": status,
        }
        if session_id:
            record["session_id"] = session_id
        if error:
            record["error"] = redact_secrets(str(error))[:500]

        line = json.dumps(record, ensure_ascii=False, default=str)
        with _LOCK:
            if not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:  # noqa: BLE001 - audit is best-effort, never break a request
        pass


__all__ = ["audit_log_path", "log_request", "redact_secrets"]
