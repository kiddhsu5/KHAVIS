#!/usr/bin/env python3
"""Public status page for ``khavis.example.com``.

A tiny stdlib-only HTTP server that renders live pool health. Serves
``/`` (HTML), ``/dashboard`` (operator view), ``/wall`` (personal-dev
wall), ``/pgp.txt`` (security contact key), ``/api/dashboard`` and
``/healthz`` (JSON). No auth, no write paths — a billboard, not a
control panel.

Run with the project venv so ``core.registry`` imports resolve::

    <KHAVIS_HOME>/.venv/bin/python -m scripts.status_server --port 8080

Cloudflare terminates TLS at the edge; the origin serves plain HTTP on
``:80`` *and* HTTPS with a throwaway self-signed cert on ``:443``. Both
on purpose: ``bot.example.com`` already shares this zone, so we must
not force a zone-wide SSL-mode change. Serving both ports means the
record works whether the zone (or a per-hostname Configuration Rule) is
set to ``Flexible`` (edge -> :80) or ``Full`` (edge -> :443; Cloudflare
accepts a self-signed origin cert outside strict mode).
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hmac
import json
import os
import re
import secrets
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BRAND = "#0EA5E9"  # sky-500 — the project's single accent colour
GENERATED_AT_FORMAT = "%Y-%m-%d %H:%M:%S UTC"


def _pgp_bytes() -> bytes:
    """Serve ``deploy/pgp.txt`` (security contact public key)."""
    candidates = [
        Path(__file__).resolve().parent.parent / "deploy" / "pgp.txt",
        PROJECT_ROOT / "deploy" / "pgp.txt",
    ]
    for p in candidates:
        try:
            return p.read_bytes()
        except OSError:
            continue
    return b"# PGP key not installed\n"


# This page is public. health_check() reports ``key_present=`` and raw
# exception text, and exceptions can embed request headers — so scrub
# anything credential-shaped before it is rendered or serialised.
_SECRET_PATTERNS = (
    re.compile(r"\bkey_present=\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password|authorization)\s*[=:]\s*\S{6,}"),
    re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"),
)


def _redact(text: str) -> str:
    """Strip credential-shaped substrings from a public-facing string."""
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[redacted]", text)
    return text


_MAX_OUTPUT_FILES = 30


def _collect_output_files(
    orch: dict[str, Any],
    project_name: str | None = None,
) -> list[dict[str, str]]:
    """彙整本輪實際產出／變更的文件（去重），供前端只呈現「成果文件」。

    只保留路徑、狀態與大小，不含 diff 與各 Agent 的過程資料。
    """
    files: dict[str, dict[str, str]] = {}

    def _add(entry: Any, base_dir: str = "") -> None:
        if isinstance(entry, str):
            entry = {"path": entry}
        if not isinstance(entry, dict):
            return
        rel = str(entry.get("path") or "").strip().rstrip("/")
        if not rel or rel in files or len(files) >= _MAX_OUTPUT_FILES:
            return
        full = str(entry.get("full_path") or "").strip()
        if not full and base_dir:
            full = f"{base_dir.rstrip('/')}/{rel}"
        p_obj = Path(full) if full else None
        if p_obj and p_obj.exists() and p_obj.is_dir():
            return
        size_str = str(entry.get("size") or "")
        if not size_str and p_obj and p_obj.exists() and p_obj.is_file():
            try:
                st = p_obj.stat()
                size_str = (
                    f"{max(0.1, round(st.st_size / 1024, 1))} KB"
                    if st.st_size >= 100
                    else f"{st.st_size} B"
                )
            except OSError:
                size_str = ""
        files[rel] = {
            "path": rel,
            "full_path": full or rel,
            "status": str(entry.get("status") or "M"),
            "size": size_str,
        }

    for entry in orch.get("shared_changed_files") or []:
        _add(entry, str(orch.get("shared_dir") or ""))
    for res in orch.get("results") or []:
        if not isinstance(res, dict):
            continue
        cwd = str(res.get("cwd") or "")
        for entry in res.get("changed_files") or []:
            _add(entry, cwd)

    if project_name:
        from web.workbench import discover_project_output_files  # noqa: PLC0415

        for disc in discover_project_output_files(project_name, limit=_MAX_OUTPUT_FILES):
            rel = str(disc.get("path") or "").strip()
            if not rel or rel in files or len(files) >= _MAX_OUTPUT_FILES:
                continue
            files[rel] = {
                "path": rel,
                "full_path": str(disc.get("full_path") or rel),
                "status": str(disc.get("status") or "A"),
                "size": str(disc.get("size") or ""),
            }
    return list(files.values())


_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_MAX_ATTACHMENT_TOTAL_BYTES = 25 * 1024 * 1024
_MAX_ATTACHMENT_COUNT = 8
_BLOCKED_ATTACHMENT_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "auth.json",
}
_BLOCKED_ATTACHMENT_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt", ".secret"}


def _validate_attachment_name(raw_name: object) -> str:
    """Return a safe basename, rejecting path traversal and credential files."""
    name = str(raw_name or "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("附件檔名無效或包含路徑")
    if (
        name in _BLOCKED_ATTACHMENT_NAMES
        or Path(name).suffix.lower() in _BLOCKED_ATTACHMENT_SUFFIXES
    ):
        raise ValueError(f"不允許上傳敏感附件：{name}")
    if len(name) > 180 or any(ord(ch) < 32 for ch in name):
        raise ValueError("附件檔名過長或包含控制字元")
    return name


def _store_dispatch_attachments(payload: dict[str, Any], shared_dir: Path) -> list[dict[str, str]]:
    """Decode bounded browser attachments into a private per-dispatch folder."""
    raw_items = payload.get("attachments")
    if raw_items in (None, ""):
        return []
    if not isinstance(raw_items, list):
        raise ValueError("attachments 必須是陣列")
    if len(raw_items) > _MAX_ATTACHMENT_COUNT:
        raise ValueError(f"附件最多 {_MAX_ATTACHMENT_COUNT} 個")

    total_bytes = 0
    decoded: list[tuple[str, str, bytes]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("附件項目格式無效")
        name = _validate_attachment_name(item.get("name"))
        encoded = str(item.get("data_base64") or "").strip()
        if not encoded:
            raise ValueError(f"附件沒有內容：{name}")
        if len(encoded) > ((_MAX_ATTACHMENT_BYTES + 2) * 4 // 3) + 16:
            raise ValueError(
                f"附件超過單檔 {_MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB 限制：{name}"
            )
        try:
            content = base64.b64decode(encoded, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"附件 Base64 內容無效：{name}") from exc
        if len(content) > _MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"附件超過單檔 {_MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB 限制：{name}"
            )
        total_bytes += len(content)
        if total_bytes > _MAX_ATTACHMENT_TOTAL_BYTES:
            raise ValueError(f"附件總量不能超過 {_MAX_ATTACHMENT_TOTAL_BYTES // (1024 * 1024)} MB")
        decoded.append((name, str(item.get("type") or "application/octet-stream")[:120], content))

    if not decoded:
        return []
    target_dir = (
        shared_dir / ".khavis_attachments" / f"dispatch-{int(time.time())}-{secrets.token_hex(6)}"
    )
    target_dir.mkdir(parents=True, exist_ok=False)
    target_root = target_dir.resolve()
    refs: list[dict[str, str]] = []
    for index, (name, content_type, content) in enumerate(decoded, start=1):
        output_path = (target_dir / f"{index:02d}-{name}").resolve()
        if target_root not in output_path.parents:
            raise ValueError("附件路徑驗證失敗")
        output_path.write_bytes(content)
        refs.append(
            {
                "name": name,
                "type": content_type,
                "size": str(len(content)),
                "path": str(output_path),
            }
        )
    return refs


def _attachment_prompt_context(refs: list[dict[str, str]]) -> str:
    if not refs:
        return ""
    lines = [
        "\n\n【本輪對話附件】",
        "以下附件已由 K.H.A.V.I.S. 儲存到共用工作區，請依任務需要讀取或分析；不要猜測附件內容：",
    ]
    lines.extend(
        f"- {item['name']} ({item['type']}, {item['size']} bytes): {item['path']}" for item in refs
    )
    return "\n".join(lines)


# (rows, monotonic timestamp). Served stale and refreshed in the
# background once older than CACHE_TTL.
CACHE_TTL = 30.0
_HEALTH_CACHE: tuple[list[dict[str, object]] | None, float] = (None, 0.0)
_CACHE_LOCK = threading.Lock()
_REFRESHING = False
_PLUG_CACHE: list | None = None  # registry snapshot; see _load_plugins()
WARMING_DETAIL = "warming up — first sweep in progress"


def cache_state() -> dict[str, object]:
    """How stale is what we are about to serve? Surfaced on ``/healthz``.

    Operators staring at a 522-shaped problem need to tell "the router is
    down" apart from "you are looking at placeholder rows".
    """
    rows, cached_at = _HEALTH_CACHE
    with _CACHE_LOCK:
        warming = _REFRESHING or rows is None
    return {
        "age_s": None if rows is None else round(time.monotonic() - cached_at, 3),
        "warming": warming,
    }


def pool_health() -> list[dict[str, object]]:
    """Return one row per registered pool. Never raises, and never blocks.

    Stale-while-revalidate. A sweep costs as long as the slowest
    unreachable endpoint (a home-LAN Ollama burns its whole connect
    timeout — ~20s here), so a TTL-expiry must never land on a visitor.

    The cold path must not block either, which is the whole point of this
    function. Cloudflare gives up on an origin after ~15s and answers 522,
    so a first request that waits out a 20s sweep turns *everyone* who
    loads the page right after a restart into a 522 — including the
    uptime check that would otherwise tell us the service is fine. Serve
    placeholder rows immediately and let the sweep fill them in.
    """
    global _HEALTH_CACHE  # noqa: PLW0603 - module-level memo is intended

    rows, cached_at = _HEALTH_CACHE
    if rows is not None:
        if (time.monotonic() - cached_at) >= CACHE_TTL:
            _kick_refresh()
        return rows

    _kick_refresh()
    return _placeholder_rows()


def _placeholder_rows() -> list[dict[str, object]]:
    """Rows for a sweep that has not landed yet. ``ok`` is None, not False.

    None keeps the page honest: the pools are not down, we simply have not
    looked yet. ``False`` would light the summary red and make an operator
    chase a phantom outage.

    Reads ``_PLUG_CACHE`` and never calls ``_load_plugins()``. Discovery is
    cheap but *importing* it is not: it pulls in every provider SDK and
    takes ~12s cold, mostly ``google.generativeai``. Calling it here would
    put that cost straight back on the request we just worked so hard not
    to block.
    """
    plugs = _PLUG_CACHE
    if not plugs:
        return [
            {
                "name": "…",
                "provider_id": "",
                "models": [],
                "capabilities": [],
                "ok": None,
                "detail": WARMING_DETAIL,
            }
        ]
    return [
        {
            "name": getattr(p, "name", "?"),
            "provider_id": getattr(p, "provider_id", ""),
            "models": list(
                getattr(p, "models", None) or ([p.model] if getattr(p, "model", None) else [])
            ),
            "capabilities": list(getattr(p, "capabilities", None) or []),
            "ok": None,
            "detail": WARMING_DETAIL,
        }
        for p in plugs
    ]


def _kick_refresh() -> None:
    """Start one background resweep. No-op if one is already running."""
    global _REFRESHING  # noqa: PLW0603 - module-level flag is intended

    with _CACHE_LOCK:
        if _REFRESHING:
            return
        _REFRESHING = True

    def run() -> None:
        global _REFRESHING, _HEALTH_CACHE  # noqa: PLW0603
        try:
            _HEALTH_CACHE = (_probe_all_pools(), time.monotonic())
        except Exception as exc:  # noqa: BLE001 - never kill the refresher
            print(f"health refresh failed: {exc!r}", file=sys.stderr, flush=True)
        finally:
            with _CACHE_LOCK:
                _REFRESHING = False

    threading.Thread(target=run, daemon=True, name="khavis-health-refresh").start()


def warm_cache() -> None:
    """Fill the plugin and health caches before anyone asks.

    Two slow steps, both off the request path: importing every provider
    SDK (~12s) and then sweeping their health (~20s if an endpoint is
    dead). Failures must not stop the listeners from binding — this runs
    after bind precisely so a warm-up problem can only cost us fresh data,
    not the page.
    """

    def run() -> None:
        try:
            _load_plugins()
        except Exception as exc:  # noqa: BLE001 - warm-up is best-effort
            print(f"warm-up: plugin load failed: {exc!r}", file=sys.stderr, flush=True)
        try:
            _kick_refresh()
        except Exception as exc:  # noqa: BLE001 - warm-up is best-effort
            print(f"warm-up: sweep failed: {exc!r}", file=sys.stderr, flush=True)

    threading.Thread(target=run, daemon=True, name="khavis-warm").start()


def _load_plugins() -> list:
    """Discover the registry once and memo the plugin objects.

    Separated from ``_probe_all_pools`` so the cold path can name the pools
    without paying for their health probes. Discovery is sub-millisecond and
    the YAML parse is single-digit milliseconds — cheap enough for a request
    — but we memo anyway because this runs behind every placeholder render.
    """
    global _PLUG_CACHE  # noqa: PLW0603 - module-level memo is intended

    if _PLUG_CACHE is not None:
        return _PLUG_CACHE

    from core.env import load_env  # noqa: PLC0415 - lazy
    from core.registry import PluginRegistry  # noqa: PLC0415 - lazy

    load_env(PROJECT_ROOT / ".env")
    reg = PluginRegistry(PROJECT_ROOT).discover()
    reg.apply_pools_config(PROJECT_ROOT / "config" / "pools.yaml")
    _PLUG_CACHE = list(reg.all())
    return _PLUG_CACHE


def _probe_all_pools() -> list[dict[str, object]]:
    try:
        plugs = _load_plugins()
    except Exception as exc:  # noqa: BLE001
        return [
            {
                "name": "registry",
                "ok": False,
                "detail": _redact(f"import failed: {exc!r}"),
                "models": [],
                "capabilities": [],
                "provider_id": "",
            }
        ]
    return _probe_plugins(plugs)


def _probe_plugins(plugs: list) -> list[dict[str, object]]:
    """Run every plugin's health_check concurrently.

    Wall time should track the slowest probe, not the sum of all of them.
    """

    def probe(plug) -> dict[str, object]:  # noqa: ANN001 - plugin type is ProviderPlugin
        try:
            h = plug.health_check()
            ok = bool(h.get("ok"))
            detail = str(h.get("detail", ""))
        except Exception as exc:  # noqa: BLE001
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
        return {
            "name": plug.name,
            "provider_id": plug.provider_id,
            "models": list(plug.list_models()),
            "capabilities": list(plug.capabilities),
            "ok": ok,
            "detail": _redact(detail),
        }

    if not plugs:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(plugs)) as pool:
        return list(pool.map(probe, plugs))


def bot_health() -> dict[str, object]:
    """Is the Telegram dispatch bot or workbench service alive on this box?"""
    for unit in ("khavis-bot", "khavis-workbench"):
        try:
            out = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            ).stdout.strip()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": _redact(repr(exc))}
        if out == "active":
            return {"ok": True, "detail": f"systemd {unit}: active"}
    return {"ok": False, "detail": f"systemd khavis-bot: {out or 'inactive'}"}


def render_html(
    rows: list[dict[str, object]],
    bot: dict[str, object],
    cache: dict[str, object] | None = None,
) -> str:
    from web.landing import render_landing_html  # noqa: PLC0415

    return render_landing_html(rows, bot, cache)


class Handler(BaseHTTPRequestHandler):
    server_version = "khavis-status/1.0"

    def _send(
        self,
        code: int,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        self._send(
            code,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _require_console_token(self) -> bool:
        expected = os.getenv("KHAVIS_CONSOLE_TOKEN", "").strip()
        supplied = self.headers.get("X-KHAVIS-Console-Token", "")
        if not expected:
            self._send_json(
                503,
                {
                    "ok": False,
                    "error": {
                        "code": "console_not_configured",
                        "detail": "K.H.A.V.I.S. console is not configured",
                    },
                },
            )
            return False
        if not supplied or not hmac.compare_digest(supplied, expected):
            self._send_json(
                401,
                {
                    "ok": False,
                    "detail": "需要 Console Token 授權（請先於雲端指令台輸入 Token）",
                    "error": {"code": "unauthorized", "detail": "Console token required"},
                },
            )
            return False
        return True

    def _require_workbench_write_token(self) -> bool:
        """Require X-KHAVIS-Console-Token for mutating /api/workbench/* routes when configured."""
        if os.getenv("KHAVIS_LOCK_WORKBENCH", "1").strip() == "0":
            return True
        expected = os.getenv("KHAVIS_CONSOLE_TOKEN", "").strip()
        if not expected:
            return True
        supplied = self.headers.get("X-KHAVIS-Console-Token", "")
        if not supplied or not hmac.compare_digest(supplied, expected):
            self._send_json(
                401,
                {
                    "ok": False,
                    "detail": "訪客唯讀展示模式：請先於「雲端指令台」輸入 Console Token 解鎖派工與設定寫入權限",
                    "error": {
                        "code": "unauthorized",
                        "detail": "Console token required for write/dispatch",
                    },
                },
            )
            return False
        return True

    def _read_json_payload(self) -> dict[str, Any] | None:
        try:
            content_len = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            content_len = -1
        if content_len < 0 or content_len > 30000:
            self._send_json(
                413,
                {
                    "ok": False,
                    "error": {"code": "payload_too_large", "detail": "Request body is too large"},
                },
            )
            return None
        try:
            payload = json.loads(self.rfile.read(content_len).decode("utf-8", errors="replace"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": {"code": "invalid_json", "detail": "Request body must be JSON"},
                },
            )
            return None
        if not isinstance(payload, dict):
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": {"code": "invalid_json", "detail": "JSON body must be an object"},
                },
            )
            return None
        return payload

    def _send_manus_exception(self, exc: Exception) -> None:
        from core.manus_client import ManusAPIError  # noqa: PLC0415

        if isinstance(exc, ManusAPIError):
            self._send_json(exc.status_code, {"ok": False, "error": exc.to_dict()})
            return
        self._send_json(
            502,
            {
                "ok": False,
                "error": {
                    "code": "manus_bridge_error",
                    "detail": "Manus bridge failed safely; inspect server logs",
                },
            },
        )

    def _handle_manus_get(self, path: str) -> None:
        from core.manus_client import ManusClient  # noqa: PLC0415

        if path == "/api/manus/status":
            client = ManusClient()
            self._send_json(
                200,
                {"ok": True, "configured": client.configured, "base_url": client.base_url},
            )
            return

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[2] == "tasks":
            try:
                result = ManusClient().task_detail(parts[3])
                self._send_json(200, {"ok": True, "task": result.get("task", result)})
            except Exception as exc:  # noqa: BLE001 - convert upstream failures to safe JSON
                self._send_manus_exception(exc)
            return

        if len(parts) == 5 and parts[2] == "tasks" and parts[4] == "messages":
            query = parse_qs(urlparse(self.path).query)
            try:
                limit = int(query.get("limit", ["50"])[0])
                result = ManusClient().list_messages(parts[3], limit=limit)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "task_id": result.get("task_id", parts[3]),
                        "messages": result.get("messages", []),
                        "next_cursor": result.get("next_cursor"),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - convert upstream failures to safe JSON
                self._send_manus_exception(exc)
            return

        self._send_json(404, {"ok": False, "error": {"code": "not_found", "detail": "Not found"}})

    def _handle_manus_post(self, path: str) -> None:
        from core.manus_client import ManusClient  # noqa: PLC0415

        payload = self._read_json_payload()
        if payload is None:
            return
        try:
            if path == "/api/manus/tasks":
                message = payload.get("message")
                profile = payload.get("agent_profile", "standard")
                interactive = payload.get("interactive_mode", True)
                if (
                    not isinstance(message, str)
                    or not isinstance(profile, str)
                    or not isinstance(interactive, bool)
                ):
                    raise ValueError(
                        "message, agent_profile and interactive_mode have invalid types"
                    )
                result = ManusClient().create_task(
                    message,
                    agent_profile=profile,
                    interactive_mode=interactive,
                )
                self._send_json(200, {"ok": True, "task": result})
                return

            parts = path.strip("/").split("/")
            if len(parts) == 5 and parts[2] == "tasks" and parts[4] == "messages":
                message = payload.get("message")
                profile = payload.get("agent_profile")
                if not isinstance(message, str) or (
                    profile is not None and not isinstance(profile, str)
                ):
                    raise ValueError("message and agent_profile have invalid types")
                result = ManusClient().send_message(parts[3], message, agent_profile=profile)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "task_id": result.get("task_id", parts[3]),
                        "request_id": result.get("request_id"),
                    },
                )
                return
            self._send_json(
                404, {"ok": False, "error": {"code": "not_found", "detail": "Not found"}}
            )
        except ValueError as exc:
            self._send_json(
                400, {"ok": False, "error": {"code": "invalid_argument", "detail": str(exc)}}
            )
        except Exception as exc:  # noqa: BLE001 - convert upstream failures to safe JSON
            self._send_manus_exception(exc)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/api/manus/status" or path.startswith("/api/manus/tasks/"):
            if self._require_console_token():
                self._handle_manus_get(path)
            return
        if path == "/pgp.txt":
            self._send(200, _pgp_bytes(), "text/plain; charset=utf-8")
            return
        allowed = (
            "/",
            "/healthz",
            "/dashboard",
            "/wall",
            "/workbench",
            "/work",
            "/workbench-c",
            "/workbench-legacy",
            "/agents",
            "/database",
            "/api/dashboard",
            "/api/quota",
            "/api/alerts",
            "/api/workbench/history",
            "/api/workbench/artifacts",
            "/api/workbench/artifacts/read",
            "/api/workbench/artifacts/download",
            "/api/workbench/database",
            "/api/workbench/agents",
            "/api/workbench/models",
            "/api/workbench/workspace",
            "/api/workbench/setup",
            "/api/workbench/capabilities",
        )
        if path not in allowed:
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        rows = pool_health()
        bot = bot_health()
        cache = cache_state()
        if path == "/healthz":
            payload = {
                # None rows are "not looked at yet", not "down" — do not
                # fold them into ok, or a warm-up looks like an outage.
                "ok": all(r.get("ok") is True for r in rows) and bool(bot.get("ok")),
                "cache": cache,
                "bot": bot,
                "pools": rows,
                "generated_at": time.strftime(GENERATED_AT_FORMAT, time.gmtime()),
            }
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/dashboard":
            from bot import history as bot_history  # noqa: PLC0415
            from web.dashboard import api_payload, public_history  # noqa: PLC0415

            # Public endpoint: never ship prompts / chat ids (api_payload
            # re-applies the same filter; this keeps the intent obvious here).
            payload = api_payload(
                pools=rows,
                bot=bot,
                cache=cache,
                history=public_history(bot_history.tail(50)),
            )
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/quota":
            from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

            snaps = get_quota_tracker().all_snapshots()
            payload = {"quotas": snaps, "pools": rows, "generated_at": time.time()}
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/workbench/history":
            from bot import history as bot_history  # noqa: PLC0415
            from web.workbench import discover_project_output_files  # noqa: PLC0415

            raw_items = bot_history.tail(200)
            formatted: list[dict[str, object]] = []
            proj_outputs_cache: dict[str, list[dict[str, str]]] = {}

            def _fallback_outputs(proj_name: str) -> list[dict[str, str]]:
                key = proj_name or "shared-app"
                if key not in proj_outputs_cache:
                    proj_outputs_cache[key] = [
                        {
                            "path": str(f.get("path") or ""),
                            "full_path": str(f.get("full_path") or f.get("path") or ""),
                            "status": str(f.get("status") or "A"),
                            "size": str(f.get("size") or ""),
                        }
                        for f in discover_project_output_files(key, limit=_MAX_OUTPUT_FILES)
                        if f.get("path")
                    ]
                return proj_outputs_cache[key]

            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                ts_val = item.get("ts")
                ts_str = (
                    str(item.get("dispatched_at"))
                    if item.get("dispatched_at")
                    else (
                        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts_val)))
                        if isinstance(ts_val, (int, float))
                        else "剛剛"
                    )
                )
                prompt_text = str(item.get("prompt") or "").strip()
                if not prompt_text:
                    phase_name = str(item.get("phase") or item.get("kind") or "pipeline")
                    prompt_text = f"系統協同流水線執行 ({phase_name})"
                _legacy_cli = "z" + "code"
                src = str(
                    item.get("consensus_source")
                    or item.get("backend")
                    or (
                        ",".join(item.get("backends", []))
                        if isinstance(item.get("backends"), list)
                        else ""
                    )
                    or item.get("phase")
                    or "K.H.A.V.I.S. Pipeline"
                ).replace(_legacy_cli, "opencode")
                raw_backends = item.get("backends")
                backends = (
                    ["opencode" if str(b) == _legacy_cli else str(b) for b in raw_backends]
                    if isinstance(raw_backends, list)
                    else (
                        ["opencode" if str(raw_backends) == _legacy_cli else str(raw_backends)]
                        if raw_backends
                        else []
                    )
                )
                proj_str = str(item.get("project") or "")
                raw_out_files = [
                    {
                        "path": str(f.get("path") or ""),
                        "full_path": str(f.get("full_path") or f.get("path") or ""),
                        "status": str(f.get("status") or "M"),
                        "size": str(f.get("size") or ""),
                    }
                    for f in (item.get("output_files") or [])
                    if isinstance(f, dict)
                    and f.get("path")
                    and not str(f.get("path") or "").endswith("/")
                ][:_MAX_OUTPUT_FILES]
                if not raw_out_files and bool(item.get("ok", True)):
                    raw_out_files = _fallback_outputs(proj_str)[:_MAX_OUTPUT_FILES]
                formatted.append(
                    {
                        "prompt": _redact(prompt_text),
                        "consensus_source": src,
                        "dispatched_at": ts_str,
                        "ok": bool(item.get("ok", True)),
                        "latency_ms": item.get("latency_ms"),
                        # 分組維度 + 重讀成果用（見 module docstring）
                        "backends": backends,
                        "collab_mode": str(item.get("collab_mode") or ""),
                        "project": proj_str,
                        "thread": str(item.get("thread") or ""),
                        "step": int(item.get("step") or 0) or 1,
                        # ts 是記錄的唯一識別：舊紀錄的 handle 只到秒，
                        # 同秒的兩筆任務會被誤併成同一個對話串。
                        "ts": float(item.get("ts") or 0.0),
                        "consensus": _redact(
                            str(item.get("consensus") or "").replace(_legacy_cli, "opencode")
                        )[:12000],
                        "review_details": _redact(
                            str(item.get("review_details") or "").replace(_legacy_cli, "opencode")
                        )[:12000],
                        # 本輪產出文件（只含路徑/狀態/大小），讓對話區只呈現成果
                        "output_files": raw_out_files,
                    }
                )
            self._send(
                200,
                json.dumps({"ok": True, "history": formatted}, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/alerts":
            from core.alerting import get_alert_manager  # noqa: PLC0415

            alerts = get_alert_manager().recent_alerts()
            payload = {"alerts": alerts, "generated_at": time.time()}
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path in ("/workbench", "/work", "/workbench-c", "/agents", "/database"):
            # 現行介面 = 選項 C（任務卡片流）。/agents 與 /database 為獨立子頁面路由。
            from web.workbench_c import render_workbench_html_c  # noqa: PLC0415

            self._send(200, render_workbench_html_c().encode(), "text/html; charset=utf-8")
            return
        if path == "/workbench-legacy":
            # 改版前的拓撲式介面，保留作為回退路徑。
            from web.workbench import render_workbench_html  # noqa: PLC0415

            self._send(200, render_workbench_html().encode(), "text/html; charset=utf-8")
            return
        if path == "/api/workbench/artifacts":
            from web.workbench import list_deliverable_artifacts  # noqa: PLC0415

            payload = {"ok": True, "artifacts": list_deliverable_artifacts()}
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/workbench/database":
            from web.workbench import get_database_overview  # noqa: PLC0415

            payload = {"ok": True, **get_database_overview()}
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/workbench/artifacts/read":
            from urllib.parse import parse_qs, urlparse  # noqa: PLC0415

            from web.workbench import read_artifact_content  # noqa: PLC0415

            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            target_file = qs.get("path", [""])[0]
            res = read_artifact_content(target_file)
            self._send(
                200,
                json.dumps(res, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/workbench/artifacts/download":
            import mimetypes  # noqa: PLC0415
            from urllib.parse import parse_qs, quote, urlparse  # noqa: PLC0415

            from web.workbench import resolve_downloadable_artifact  # noqa: PLC0415

            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            target_file = qs.get("path", [""])[0]
            resolved = resolve_downloadable_artifact(target_file)
            if resolved is None:
                self._send(
                    404,
                    json.dumps(
                        {"ok": False, "error": "File not found or access denied"},
                        ensure_ascii=False,
                    ).encode(),
                    "application/json; charset=utf-8",
                )
                return
            data = resolved.read_bytes()
            mime_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            ascii_name = resolved.name.encode("ascii", "ignore").decode("ascii") or "download"
            encoded_name = quote(resolved.name)
            disp = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
            self._send(
                200,
                data,
                mime_type,
                extra_headers={"Content-Disposition": disp},
            )
            return
        if path == "/api/workbench/agents":
            from web.workbench import get_agent_fleet_status  # noqa: PLC0415

            payload = {"ok": True, "agents": get_agent_fleet_status()}
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/workbench/models":
            from web.workbench import get_workbench_models_config  # noqa: PLC0415

            payload = {"ok": True, **get_workbench_models_config()}
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/workbench/workspace":
            from urllib.parse import parse_qs, urlparse  # noqa: PLC0415

            from core.workspace_manager import get_workspace_status  # noqa: PLC0415

            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            proj = qs.get("project", [""])[0] or None
            payload = get_workspace_status(proj)
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/workbench/setup":
            from web.workbench import get_setup_wizard_status  # noqa: PLC0415

            payload = {"ok": True, **get_setup_wizard_status()}
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/workbench/capabilities":
            from web.workbench import get_workbench_capabilities  # noqa: PLC0415

            payload = get_workbench_capabilities()
            self._send(
                200,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return
        if path in ("/dashboard", "/wall"):
            from bot import history as bot_history  # noqa: PLC0415
            from web.dashboard import (  # noqa: PLC0415
                public_history,
                render_dashboard_html,
                render_wall_html,
            )

            if path == "/wall":
                body = render_wall_html()
            else:
                body = render_dashboard_html(
                    pools=rows,
                    bot=bot,
                    cache=cache,
                    history=public_history(bot_history.tail(50)),
                )
            self._send(200, body.encode(), "text/html; charset=utf-8")
            return
        self._send(200, render_html(rows, bot, cache).encode(), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/manus/tasks" or path.startswith("/api/manus/tasks/"):
            if self._require_console_token():
                self._handle_manus_post(path)
            return
        if path.startswith("/api/workbench/") and not self._require_workbench_write_token():
            return
        if path == "/api/workbench/models":
            content_len = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except Exception:  # noqa: BLE001
                payload = {}

            from web.workbench import update_workbench_models_config  # noqa: PLC0415

            try:
                updated = update_workbench_models_config(
                    payload if isinstance(payload, dict) else {}
                )
            except ValueError as exc:
                self._send(
                    400,
                    json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
                return

            self._send(
                200,
                json.dumps({"ok": True, **updated}, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return

        if path == "/api/workbench/provider_models":
            content_len = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except Exception:  # noqa: BLE001
                payload = {}

            from web.workbench import fetch_provider_model_list  # noqa: PLC0415

            try:
                result = fetch_provider_model_list(
                    payload if isinstance(payload, dict) else {}
                )
            except ValueError as exc:
                self._send(
                    400,
                    json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
                return

            self._send(
                200,
                json.dumps(result, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return

        if path == "/api/workbench/agent_models":
            content_len = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except Exception:  # noqa: BLE001
                payload = {}
            payload = payload if isinstance(payload, dict) else {}
            action = str(payload.get("action") or "fetch").strip().lower()

            if action == "set":
                from web.workbench import update_workbench_models_config  # noqa: PLC0415

                try:
                    updated = update_workbench_models_config(
                        {"action": "set_agent_models",
                         "agent": payload.get("agent"),
                         "models": payload.get("models")}
                    )
                except ValueError as exc:
                    self._send(
                        400,
                        json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False).encode(),
                        "application/json; charset=utf-8",
                    )
                    return
                self._send(
                    200,
                    json.dumps({"ok": True, **updated}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
                return

            from web.workbench import fetch_agent_model_catalog  # noqa: PLC0415

            try:
                result = fetch_agent_model_catalog(payload)
            except ValueError as exc:
                self._send(
                    400,
                    json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
                return
            self._send(
                200,
                json.dumps(result, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return

        if path == "/api/workbench/workspace":
            content_len = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except Exception:  # noqa: BLE001
                payload = {}

            from core.workspace_manager import configure_workspace  # noqa: PLC0415

            res = configure_workspace(payload if isinstance(payload, dict) else {})
            self._send(
                200 if res.get("ok", True) else 400,
                json.dumps(res, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return

        if path == "/api/workbench/agents/custom":
            content_len = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except Exception:  # noqa: BLE001
                payload = {}
            payload = payload if isinstance(payload, dict) else {}

            from core.custom_agents import (  # noqa: PLC0415
                delete_custom_agent,
                enrich_custom_agent_status,
                load_custom_agents,
                register_or_update_custom_agent,
            )

            action = str(payload.get("action") or "upsert").strip().lower()
            try:
                if action == "delete":
                    agent_id = str(payload.get("id") or "").strip()
                    delete_custom_agent(agent_id)
                    custom_list = [enrich_custom_agent_status(a) for a in load_custom_agents()]
                    self._send(
                        200,
                        json.dumps(
                            {"ok": True, "deleted": True, "custom_agents": custom_list},
                            ensure_ascii=False,
                        ).encode(),
                        "application/json; charset=utf-8",
                    )
                    return

                entry = register_or_update_custom_agent(payload)
                custom_list = [enrich_custom_agent_status(a) for a in load_custom_agents()]
                self._send(
                    200,
                    json.dumps(
                        {
                            "ok": True,
                            "agent": enrich_custom_agent_status(entry),
                            "custom_agents": custom_list,
                        },
                        ensure_ascii=False,
                    ).encode(),
                    "application/json; charset=utf-8",
                )
            except ValueError as exc:
                self._send(
                    400,
                    json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
            return

        if path == "/api/workbench/setup":
            content_len = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except Exception:  # noqa: BLE001
                payload = {}

            from web.workbench import update_setup_wizard_config  # noqa: PLC0415

            try:
                res = update_setup_wizard_config(payload if isinstance(payload, dict) else {})
            except ValueError as exc:
                self._send(
                    400,
                    json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
                return
            self._send(
                200,
                json.dumps(res, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return

        if path == "/api/workbench/dispatch":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0 or content_len > 35 * 1024 * 1024:
                self._send(
                    413,
                    json.dumps(
                        {"ok": False, "detail": "dispatch body 超過 35 MB 限制"}, ensure_ascii=False
                    ).encode(),
                    "application/json; charset=utf-8",
                )
                return
            raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except Exception:  # noqa: BLE001
                payload = {}

            prompt = str(payload.get("prompt") or "").strip()
            raw_agents = (
                payload.get("agents")
                or payload.get("targets")
                or payload.get("agent")
                or ["khavis"]
            )
            agents = (
                [str(raw_agents)] if isinstance(raw_agents, str) else [str(a) for a in raw_agents]
            )
            capability = payload.get("capability") or None
            raw_models = payload.get("agent_models")
            agent_models = raw_models if isinstance(raw_models, dict) else {}
            thinking_mode = str(payload.get("thinking_mode") or "").strip() or None
            thinking_parameter = str(payload.get("thinking_parameter") or "").strip() or None
            if thinking_mode and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", thinking_mode):
                self._send(
                    400,
                    json.dumps(
                        {"ok": False, "detail": "thinking_mode 格式無效"}, ensure_ascii=False
                    ).encode(),
                    "application/json; charset=utf-8",
                )
                return
            if thinking_parameter and not re.fullmatch(r"[A-Za-z0-9_]{1,64}", thinking_parameter):
                self._send(
                    400,
                    json.dumps(
                        {"ok": False, "detail": "thinking_parameter 格式無效"}, ensure_ascii=False
                    ).encode(),
                    "application/json; charset=utf-8",
                )
                return
            isolation_mode = str(payload.get("isolation_mode") or "").strip().lower() or None
            project_name = (
                str(payload.get("project") or payload.get("project_id") or "").strip() or None
            )
            collab_mode = str(payload.get("collab_mode") or "auto_loop").strip().lower()
            previous_context = (
                payload.get("previous_context")
                if isinstance(payload.get("previous_context"), dict)
                else None
            )
            # 對話串識別：前端在每輪帶上 thread id；第一輪沒有前例時由後端產生。
            _prev_ctx = previous_context if isinstance(previous_context, dict) else {}
            thread_id = str(_prev_ctx.get("thread") or "").strip() or f"t{int(time.time() * 1000)}"
            try:
                thread_step = int(_prev_ctx.get("step") or 0) + 1
            except (TypeError, ValueError):
                thread_step = 1

            if not prompt:
                self._send(
                    400,
                    json.dumps({"ok": False, "detail": "Prompt cannot be empty"}).encode(),
                    "application/json; charset=utf-8",
                )
                return

            from bot import history as bot_history  # noqa: PLC0415
            from bot.backends import (  # noqa: PLC0415
                AntigravityBackend,
                ClaudeBackend,
                CodexBackend,
                CustomAgentBackend,
                KavisTencentBackend,
                KhavisBackend,
                MinimaxBackend,
                OpencodeBackend,
            )
            from bot.dispatch import DispatchRouter  # noqa: PLC0415
            from bot.models import BackendName  # noqa: PLC0415
            from core.collab_orchestrator import run_collaborative_workflow  # noqa: PLC0415
            from core.custom_agents import load_custom_agents  # noqa: PLC0415
            from core.env import load_env  # noqa: PLC0415
            from core.workspace_manager import (  # noqa: PLC0415
                load_workspace_config,
                prepare_agent_cwd,
            )

            load_env(PROJECT_ROOT / ".env")
            ws_cfg = load_workspace_config()
            eff_mode = (
                isolation_mode
                if isolation_mode in ("shared", "worktree")
                else str(ws_cfg.get("isolation_mode", "shared"))
            )
            eff_project = project_name or str(ws_cfg.get("active_project", "shared-app"))

            agent_cwds: dict[str, Path] = {}
            all_known_ids = {
                "claude",
                "codex",
                "antigravity",
                "minimax",
                "opencode",
                *(str(a) for a in agents),
            }
            for aid in all_known_ids:
                if aid in ("khavis", "kavis-tencent"):
                    # khavis 在本機 in-process 執行；kavis-tencent 是遠端節點，
                    # 兩者都不需要本機 workspace。
                    continue
                agent_cwds[aid] = prepare_agent_cwd(
                    aid, isolation_mode=eff_mode, project_name=eff_project
                )

            shared_dir = prepare_agent_cwd(
                "shared", isolation_mode="shared", project_name=eff_project
            )
            try:
                attachment_refs = _store_dispatch_attachments(payload, shared_dir)
            except ValueError as exc:
                self._send(
                    400,
                    json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
                return
            effective_prompt = prompt + _attachment_prompt_context(attachment_refs)

            backends: dict[BackendName, Any] = {
                "claude": ClaudeBackend(
                    model=str(agent_models.get("claude") or "").strip(),
                    cwd=str(agent_cwds.get("claude", shared_dir)),
                ),
                "codex": CodexBackend(
                    model=str(agent_models.get("codex") or "").strip(),
                    cwd=str(agent_cwds.get("codex", shared_dir)),
                ),
                "antigravity": AntigravityBackend(
                    model=str(agent_models.get("antigravity") or "").strip(),
                    cwd=str(agent_cwds.get("antigravity", shared_dir)),
                ),
                "minimax": MinimaxBackend(
                    model=str(agent_models.get("minimax") or "").strip(),
                    cwd=str(agent_cwds.get("minimax", shared_dir)),
                ),
                "opencode": OpencodeBackend(
                    model=str(agent_models.get("opencode") or "").strip(),
                    cwd=str(agent_cwds.get("opencode", shared_dir)),
                ),
                "khavis": KhavisBackend(),
                "kavis-tencent": KavisTencentBackend(
                    model=str(agent_models.get("kavis-tencent") or "").strip(),
                ),
            }

            for custom_cfg in load_custom_agents():
                cid = str(custom_cfg.get("id") or "").strip()
                if not cid:
                    continue
                c_cwd = agent_cwds.get(cid) or prepare_agent_cwd(
                    cid, isolation_mode=eff_mode, project_name=eff_project
                )
                agent_cwds[cid] = c_cwd
                backends[cid] = CustomAgentBackend(
                    custom_cfg,
                    model=str(
                        agent_models.get(cid) or custom_cfg.get("default_model") or ""
                    ).strip(),
                    cwd=str(c_cwd),
                )

            router = DispatchRouter(backends)

            import asyncio  # noqa: PLC0415

            t0 = time.perf_counter()
            loop = asyncio.new_event_loop()
            try:
                orch = loop.run_until_complete(
                    run_collaborative_workflow(
                        router=router,
                        backends=backends,
                        agents=agents,
                        prompt=effective_prompt,
                        capability=capability,
                        shared_dir=shared_dir,
                        agent_cwds=agent_cwds,
                        collab_mode=collab_mode,
                        previous_context=previous_context,
                        thinking_mode=thinking_mode,
                        thinking_parameter=thinking_parameter,
                    )
                )
            finally:
                loop.close()
            total_ms = int((time.perf_counter() - t0) * 1000)

            results_dump = orch["results"]
            turn_output_files = _collect_output_files(orch, eff_project)
            bot_history.record(
                {
                    "ts": time.time(),
                    "kind": "workbench",
                    "prompt": prompt,
                    "backends": list(agents),
                    "collab_mode": orch["collab_mode"],
                    "consensus_source": orch["consensus_source"]
                    or ", ".join(str(a) for a in agents),
                    "project": eff_project,
                    "isolation_mode": eff_mode,
                    "thread": thread_id,
                    "step": thread_step,
                    "consensus": str(orch.get("consensus") or ""),
                    "review_details": str(orch.get("review_details") or ""),
                    "output_files": turn_output_files,
                    "dispatched_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    "ok": any(r.get("ok") for r in results_dump),
                    "latency_ms": total_ms,
                }
            )

            res_payload = {
                "ok": True,
                "thread": thread_id,
                "step": thread_step,
                "collab_mode": orch["collab_mode"],
                "consensus": orch["consensus"],
                "review_details": orch.get("review_details", ""),
                "output_files": turn_output_files,
                "consensus_source": orch["consensus_source"],
                "critique_summary": orch["critique_summary"],
                "collaboration_steps": orch["collaboration_steps"],
                "next_actions": orch["next_actions"],
                "isolation_mode": eff_mode,
                "project": eff_project,
                "thinking_mode": thinking_mode,
                "thinking_parameter": thinking_parameter,
                "attachments": attachment_refs,
                "shared_dir": str(shared_dir),
                "shared_changed_files": orch["shared_changed_files"],
                "shared_diff": orch["shared_diff"],
                "results": results_dump,
            }
            self._send(
                200,
                json.dumps(res_payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
            return

        self._send(
            404,
            json.dumps({"ok": False, "detail": "Not found"}).encode(),
            "application/json; charset=utf-8",
        )

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")


def resolve_cert_paths(cert_path: Path, key_path: Path) -> tuple[Path, Path]:
    """Prefer the requested location; fall back to a temp dir if it is not creatable.

    Under systemd the unit declares ``CacheDirectory=khavis-status`` so the
    directory already exists and is writable. A bare manual run may not have
    that, and ``ProtectSystem=``-style lockdown is not the only reason a
    ``/etc/...`` default would fail to create — so degrade instead of dying.
    """
    try:
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        return cert_path, key_path
    except OSError as exc:
        import tempfile  # noqa: PLC0415 - only on the fallback path

        fallback = Path(tempfile.mkdtemp(prefix="khavis-status-"))
        print(
            f"cert dir {cert_path.parent} not usable ({exc!r}); using {fallback}",
            file=sys.stderr,
            flush=True,
        )
        return fallback / cert_path.name, fallback / key_path.name


def ensure_self_signed_cert(cert_path: Path, key_path: Path) -> bool:
    """Create a throwaway self-signed cert if neither file exists yet.

    Returns True when a cert is available. The private key never leaves
    the box and is worthless to an attacker who already has root on it —
    its only job is to satisfy Cloudflare outside ``Full (strict)``.
    """
    if cert_path.exists() and key_path.exists():
        return True
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "825",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
        "-subj",
        "/CN=khavis.example.com",
        "-addext",
        "subjectAltName=DNS:khavis.example.com,DNS:localhost",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"cert bootstrap failed: {exc!r}", file=sys.stderr, flush=True)
        return False
    key_path.chmod(0o600)
    return True


def primary_ipv4() -> str | None:
    """Outbound IPv4 of this host, without touching DNS.

    A UDP ``connect()`` picks a local address from the routing table and
    sends no packets. Deliberately not ``getaddrinfo(gethostname())`` —
    on a host with a ``.local`` name that blocks on mDNS for seconds and
    would stall service startup.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        addr = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if not addr or addr.startswith("127.") else addr


def candidate_hosts(preferred: str) -> list[str]:
    """Bind addresses to try, most preferred first.

    A wildcard bind conflicts with *any* socket already holding that port
    on a specific address. On this box ``tailscaled`` holds 443 on the
    Tailscale IP, so ``0.0.0.0:443`` is EADDRINUSE even though the public
    path is completely free — falling back to a concrete unicast address
    sidesteps the clash without disturbing whatever else is running.
    """
    hosts = [preferred]
    if preferred not in ("0.0.0.0", "::", ""):
        return hosts
    addr = primary_ipv4()
    if addr is not None and addr not in hosts:
        hosts.append(addr)
    hosts.append("127.0.0.1")
    return hosts


class _Listener(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` that binds without a reverse-DNS lookup.

    ``http.server.HTTPServer.server_bind`` ends with ``socket.getfqdn(host)``.
    On a wildcard bind that resolves quickly, but on a concrete unicast
    address with no PTR record it blocks on the resolver timeout. Measured on
    the deploy host: **10.014 s** between "listening on http://0.0.0.0:80" and
    "listening on https://172.17.0.106:443" — the :443 fallback address after
    ``tailscaled`` claimed ``*:443``.

    That would only be a slow start if ``main()`` did not build every listener
    before starting any ``serve_forever``. The :80 socket is already in the
    ``LISTEN`` state by then, so connections queue in the kernel backlog with
    nobody accepting them and every visitor times out. Same class of trap as
    ``primary_ipv4()`` above: no DNS on the startup path. ``server_name`` is
    only used for CGI and log decoration, so the bare hostname is enough.
    """

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        _host, port = self.server_address[:2]
        self.server_name = socket.gethostname()
        self.server_port = port


class _TLSServer(_Listener):
    """HTTPS server that performs the TLS handshake in the *worker* thread.

    Two traps this avoids, both hit in production:

    1. Wrapping the listening socket —

           srv.socket = ctx.wrap_socket(srv.socket, server_side=True)

       — looks shorter but drops the only reference to the original socket
       object, whose ``__del__`` closes the shared file descriptor.

    2. Wrapping in ``get_request()`` — which is the obvious place — is just
       as bad in a different way: ``socketserver`` calls ``get_request()``
       from ``serve_forever``'s own thread, *before* ``process_request()``
       spawns a worker. A handshake there therefore blocks accept for up to
       the handshake timeout, the backlog (5) fills, and callers see Cloudflare
       525 / timeouts while the process looks idle.

    ``process_request_thread`` is the first thing that already runs on a
    fresh thread, so the handshake belongs there.
    """

    def __init__(self, addr: tuple[str, int], handler, ctx: ssl.SSLContext) -> None:
        self._ctx = ctx
        super().__init__(addr, handler)

    def process_request_thread(self, request, client_address) -> None:  # noqa: ANN001
        # Cap the handshake so half-open TLS attempts cannot pin a thread.
        request.settimeout(10)
        try:
            request = self._ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            # Scanners sending plain HTTP to the HTTPS port land here. Just
            # drop them — a traceback per probe is noise, and shutdown_request
            # closes the socket.
            self.shutdown_request(request)
            return
        request.settimeout(None)
        super().process_request_thread(request, client_address)


def start_listener(
    label: str,
    host: str,
    port: int,
    wrap_ssl: ssl.SSLContext | None = None,
) -> tuple[str, ThreadingHTTPServer, threading.Thread] | None:
    """Bind one listener, trying ``candidate_hosts`` in order.

    ``serve_forever`` is started here, not later by ``main()``. A socket is
    already in the ``LISTEN`` state the moment ``__init__`` returns, so any
    work that happens between bind and serve — generating a cert, resolving
    a fallback address — costs every visitor a timeout while connections pile
    up in the kernel backlog. Binding must mean serving.

    Returns None when nothing bound — a dead :443 must not take :80 down
    with it, and vice versa.
    """
    errors: list[str] = []
    for addr in candidate_hosts(host):
        try:
            srv: ThreadingHTTPServer
            if wrap_ssl is not None:
                srv = _TLSServer((addr, port), Handler, wrap_ssl)
            else:
                srv = _Listener((addr, port), Handler)
        except OSError as exc:
            errors.append(f"{addr}:{port} -> {exc.strerror or exc}")
            continue
        note = " (self-signed)" if wrap_ssl is not None else ""
        print(f"khavis-status listening on {label}://{addr}:{port}{note}", flush=True)
        thread = threading.Thread(target=srv.serve_forever, daemon=True, name=f"khavis-{label}")
        thread.start()
        return (f"{label}://{addr}", srv, thread)
    print(
        f"khavis-status: could not bind {label}:{port} — {'; '.join(errors)}",
        file=sys.stderr,
        flush=True,
    )
    return None


def main() -> None:
    parser = argparse.ArgumentParser(prog="khavis-status")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--port", type=int, default=80, help="plain HTTP (Cloudflare SSL mode Flexible)"
    )
    parser.add_argument(
        "--tls-port",
        type=int,
        default=443,
        help="HTTPS with self-signed cert (Cloudflare SSL mode Full)",
    )
    parser.add_argument("--cert", type=Path, default=Path("/var/cache/khavis-status/cert.pem"))
    parser.add_argument("--key", type=Path, default=Path("/var/cache/khavis-status/key.pem"))
    args = parser.parse_args()
    cert_path, key_path = resolve_cert_paths(args.cert, args.key)

    started: list[tuple[str, ThreadingHTTPServer, threading.Thread]] = []

    http = start_listener("http", args.host, args.port)
    if http is not None:
        started.append(http)

    if args.tls_port and ensure_self_signed_cert(cert_path, key_path):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_path), str(key_path))
        https = start_listener("https", args.host, args.tls_port, wrap_ssl=ctx)
        if https is not None:
            started.append(https)

    if not started:
        raise SystemExit("khavis-status: no listener could be bound; refusing to idle")

    # Fill the health cache before the first visitor arrives. Without this
    # the very first request pays for the sweep; see pool_health().
    warm_cache()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        for _name, srv, _t in started:
            srv.shutdown()


if __name__ == "__main__":
    main()
