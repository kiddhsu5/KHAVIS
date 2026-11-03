"""Coding-plan quota probes for pools that expose a balance endpoint.

Verified 2026-11-03 against the deployed host (response shape only; keys stay
in the environment and are never logged):

* MiniMax Token Plan — ``GET https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains``
  with ``Authorization: Bearer <MINIMAX_API_KEY>``. Coding usage is the
  ``model_name == "general"`` row. Interval/weekly counts may be 0 while
  ``current_*_remaining_percent`` is populated; prefer the percent fields.
* GLM Coding Plan — ``GET https://open.bigmodel.cn/api/monitor/usage/quota/limit``
  with ``Authorization: <ZHIPUAI_API_KEY>`` (no ``Bearer`` prefix). Live payload
  uses ``CREDIT_LIMIT`` items, not the older ``TOKENS_LIMIT`` example.
  ``percentage`` is used-percent. ``unit=3, number=5`` is the 5-hour window;
  ``unit=6, number=1`` is the weekly window.

JUAPI and Antigravity have no confirmed quota endpoint. This module does not
invent one.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

log = logging.getLogger(__name__)

MINIMAX_URL = "https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains"
GLM_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"

POOL_MINIMAX = "MiniMax-M3"
POOL_GLM = "GLM-5.3"


def _ratio_from_percent(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n > 1:
        n = n / 100.0
    return max(0.0, min(1.0, n))


def parse_minimax_remains(data: dict[str, Any]) -> dict[str, Any]:
    """Parse a MiniMax coding-plan remains payload. Does not call the network."""
    rows = data.get("model_remains") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "provider": "minimax", "error": "no model_remains"}
    general = next(
        (row for row in rows if isinstance(row, dict) and row.get("model_name") == "general"),
        None,
    )
    if not isinstance(general, dict):
        general = rows[0] if isinstance(rows[0], dict) else None
    if not isinstance(general, dict):
        return {"ok": False, "provider": "minimax", "error": "general row missing"}

    windows: list[dict[str, Any]] = []
    interval = _ratio_from_percent(general.get("current_interval_remaining_percent"))
    weekly = _ratio_from_percent(general.get("current_weekly_remaining_percent"))
    if interval is not None:
        windows.append(
            {
                "label": "本段",
                "remaining_ratio": interval,
                "reset_at_ms": general.get("end_time"),
            }
        )
    if weekly is not None:
        windows.append(
            {
                "label": "本週",
                "remaining_ratio": weekly,
                "reset_at_ms": general.get("weekly_end_time"),
            }
        )
    ratios = [w["remaining_ratio"] for w in windows]
    return {
        "ok": bool(ratios),
        "provider": "minimax",
        "plan": "MiniMax Coding Plan",
        "pool": POOL_MINIMAX,
        "remaining_ratio": min(ratios) if ratios else None,
        "windows": windows,
        "error": "" if ratios else "percent fields missing",
    }


def _glm_label(item: dict[str, Any]) -> str:
    if str(item.get("type") or "") == "TOKENS_LIMIT":
        return "Token"
    unit, number = item.get("unit"), item.get("number")
    if unit == 3 and number == 5:
        return "5 小時"
    if unit == 6 and number == 1:
        return "本週"
    return str(item.get("type") or "額度")


def parse_glm_quota(data: dict[str, Any]) -> dict[str, Any]:
    """Parse a GLM coding-plan quota payload. Does not call the network."""
    body = data.get("data") if isinstance(data, dict) else None
    limits = body.get("limits") if isinstance(body, dict) else None
    if not isinstance(limits, list) or not limits:
        return {"ok": False, "provider": "glm", "error": "no limits"}
    windows: list[dict[str, Any]] = []
    for item in limits:
        if not isinstance(item, dict):
            continue
        ratio = None
        remaining = item.get("remaining")
        total = item.get("usage")
        try:
            if remaining is not None and total is not None and float(total) > 0:
                ratio = max(0.0, min(1.0, float(remaining) / float(total)))
        except (TypeError, ValueError):
            ratio = None
        if ratio is None and item.get("percentage") is not None:
            used = _ratio_from_percent(item.get("percentage"))
            ratio = None if used is None else max(0.0, 1.0 - used)
        if ratio is None:
            continue
        windows.append(
            {
                "label": _glm_label(item),
                "remaining_ratio": ratio,
                "remaining": remaining,
                "total": total,
                "reset_at_ms": item.get("nextResetTime"),
            }
        )
    level = str(body.get("level") or "").strip()
    ratios = [w["remaining_ratio"] for w in windows]
    return {
        "ok": bool(ratios),
        "provider": "glm",
        "plan": "GLM Coding Plan" + (f" · {level}" if level else ""),
        "pool": POOL_GLM,
        "remaining_ratio": min(ratios) if ratios else None,
        "windows": windows,
        "error": "" if ratios else "limits missing remaining",
    }


def _get_json(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("quota payload is not an object")
    return payload


def query_minimax(api_key: str, timeout: float = 8.0) -> dict[str, Any]:
    data = _get_json(
        MINIMAX_URL,
        {"Authorization": "Bearer " + api_key, "Accept": "application/json"},
        timeout,
    )
    return parse_minimax_remains(data)


def query_glm(api_key: str, timeout: float = 8.0) -> dict[str, Any]:
    data = _get_json(
        GLM_URL,
        {"Authorization": api_key, "Accept": "application/json"},
        timeout,
    )
    return parse_glm_quota(data)


def refresh_coding_plan_quotas(
    tracker: Any,
    *,
    ttl: float = 60.0,
    force: bool = False,
    getenv: Callable[[str], str] | None = None,
    query_minimax_fn: Callable[[str], dict[str, Any]] = query_minimax,
    query_glm_fn: Callable[[str], dict[str, Any]] = query_glm,
) -> dict[str, Any]:
    """Refresh MiniMax / GLM snapshots. Failures are recorded, not raised."""
    now = time.time()
    last = float(getattr(tracker, "_plan_refresh_at", 0.0) or 0.0)
    if not force and (now - last) < ttl:
        return {"ok": True, "cached": True}
    tracker._plan_refresh_at = now  # noqa: SLF001 - cache lives with the tracker
    read_env = getenv or os.environ.get
    probes = (
        (POOL_MINIMAX, "MINIMAX_API_KEY", query_minimax_fn),
        (POOL_GLM, "ZHIPUAI_API_KEY", query_glm_fn),
    )
    written: list[str] = []
    for pool, env_key, query in probes:
        key = str(read_env(env_key) or "").strip()
        if not key:
            tracker.record_api_result(
                pool,
                {"note": "未設定 " + env_key, "source": "coding-plan"},
            )
            continue
        try:
            parsed = query(key)
        except Exception as exc:  # noqa: BLE001 - quota outage must not break /api/quota
            log.debug("coding-plan quota failed for %s: %s", pool, type(exc).__name__)
            tracker.record_api_result(
                pool,
                {"note": "查詢失敗", "source": "coding-plan"},
            )
            continue
        if not parsed.get("ok"):
            tracker.record_api_result(
                pool,
                {"note": str(parsed.get("error") or "無額度資料"), "source": "coding-plan"},
            )
            continue
        tracker.record_api_result(
            pool,
            {
                "remaining_ratio": parsed.get("remaining_ratio"),
                "plan": parsed.get("plan"),
                "windows": parsed.get("windows") or [],
                "note": "",
                "source": "coding-plan",
            },
        )
        written.append(pool)
    return {"ok": True, "cached": False, "updated": written}
