"""Active quota and rate-limit tracking module for K.H.A.V.I.S.

Monitors upstream provider headers (e.g. ``x-ratelimit-*``, ``anthropic-ratelimit-*``)
and queries balance APIs (e.g. OpenRouter, SiliconFlow) to detect exhaustion BEFORE
a 429 error occurs, enabling zero-interruption proactive routing.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

#: Default token threshold under which a pool is considered exhausted
DEFAULT_TOKEN_BUFFER = 150
#: Default request threshold under which a pool is considered exhausted
DEFAULT_REQUEST_BUFFER = 1
#: Cache TTL for balance API queries (seconds)
DEFAULT_API_CACHE_TTL = 60.0


@dataclass
class RateLimitSnapshot:
    """Current rate-limit and quota status for a single model pool."""

    pool_name: str
    remaining_requests: int | None = None
    limit_requests: int | None = None
    reset_requests_s: float | None = None
    remaining_tokens: int | None = None
    limit_tokens: int | None = None
    reset_tokens_s: float | None = None
    balance_usd: float | None = None
    is_free_tier: bool | None = None
    updated_at: float = field(default_factory=time.time)
    source: str = "header"  # "header", "api", "manual"

    def is_exhausted(
        self,
        token_buffer: int = DEFAULT_TOKEN_BUFFER,
        request_buffer: int = DEFAULT_REQUEST_BUFFER,
        now: float | None = None,
    ) -> bool:
        """Return True if this pool should be avoided due to imminent or current exhaustion."""
        current_time = time.time() if now is None else now

        # Balance check (for paid pools with known USD balance)
        if self.balance_usd is not None and self.balance_usd <= 0.0:
            return True

        # Request count limit check
        if self.remaining_requests is not None and self.remaining_requests <= request_buffer:
            if self.reset_requests_s is not None:
                elapsed = current_time - self.updated_at
                if elapsed < self.reset_requests_s:
                    return True
            else:
                return True

        # Token count limit check
        if self.remaining_tokens is not None and self.remaining_tokens <= token_buffer:
            if self.reset_tokens_s is not None:
                elapsed = current_time - self.updated_at
                if elapsed < self.reset_tokens_s:
                    return True
            else:
                return True

        return False

    def seconds_until_reset(self, now: float | None = None) -> float:
        """Return estimated seconds until rate limits reset, or 0.0 if not exhausted."""
        current_time = time.time() if now is None else now
        elapsed = current_time - self.updated_at
        resets: list[float] = []

        if self.reset_requests_s is not None:
            rem = self.reset_requests_s - elapsed
            if rem > 0:
                resets.append(rem)
        if self.reset_tokens_s is not None:
            rem = self.reset_tokens_s - elapsed
            if rem > 0:
                resets.append(rem)

        return max(resets) if resets else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert snapshot to dictionary for metrics/dashboard."""
        return {
            "pool_name": self.pool_name,
            "remaining_requests": self.remaining_requests,
            "limit_requests": self.limit_requests,
            "reset_requests_s": self.reset_requests_s,
            "remaining_tokens": self.remaining_tokens,
            "limit_tokens": self.limit_tokens,
            "reset_tokens_s": self.reset_tokens_s,
            "balance_usd": self.balance_usd,
            "is_free_tier": self.is_free_tier,
            "is_exhausted": self.is_exhausted(),
            "seconds_until_reset": round(self.seconds_until_reset(), 2),
            "updated_at": self.updated_at,
            "source": self.source,
        }


def _parse_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None


def _parse_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None


def parse_reset_offset(val: Any, now: float | None = None) -> float | None:
    """Parse reset timestamps or durations into seconds from now.

    Supports:
    * Durations like "20ms", "500ms", "15s", "2m", "1h"
    * Float/int seconds (e.g. 0.5, 30)
    * Epoch timestamps (e.g. 1729000000)
    * ISO 8601 timestamps
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None

    current_time = time.time() if now is None else now

    # Human-readable duration suffixes
    m_ms = re.match(r"^(\d+(?:\.\d+)?)\s*ms$", s, re.IGNORECASE)
    if m_ms:
        return max(0.0, float(m_ms.group(1)) / 1000.0)

    m_s = re.match(r"^(\d+(?:\.\d+)?)\s*s$", s, re.IGNORECASE)
    if m_s:
        return max(0.0, float(m_s.group(1)))

    m_m = re.match(r"^(\d+(?:\.\d+)?)\s*m$", s, re.IGNORECASE)
    if m_m:
        return max(0.0, float(m_m.group(1)) * 60.0)

    m_h = re.match(r"^(\d+(?:\.\d+)?)\s*h$", s, re.IGNORECASE)
    if m_h:
        return max(0.0, float(m_h.group(1)) * 3600.0)

    # Pure numeric
    try:
        num = float(s)
        # Large numbers (> 1e9) are unix epoch timestamps
        if num > 1_000_000_000:
            return max(0.0, num - current_time)
        return max(0.0, num)
    except ValueError:
        pass

    # Try ISO date parsing
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        epoch = dt.astimezone(UTC).timestamp()
        return max(0.0, epoch - current_time)
    except Exception:  # noqa: BLE001
        return None


def parse_rate_limit_headers(headers: Mapping[str, Any], pool_name: str = "") -> RateLimitSnapshot:
    """Extract standard and vendor-specific rate-limit headers into a RateLimitSnapshot."""
    # Normalize headers to lowercase keys
    h = {str(k).lower(): str(v).strip() for k, v in headers.items()}

    # 1. Request Limits
    rem_req = None
    for k in (
        "x-ratelimit-remaining-requests",
        "anthropic-ratelimit-requests-remaining",
        "ratelimit-remaining-requests",
        "ratelimit-remaining",
        "x-ratelimit-remaining",
    ):
        if k in h:
            rem_req = _parse_int(h[k])
            if rem_req is not None:
                break

    limit_req = None
    for k in (
        "x-ratelimit-limit-requests",
        "anthropic-ratelimit-requests-limit",
        "ratelimit-limit-requests",
        "ratelimit-limit",
        "x-ratelimit-limit",
    ):
        if k in h:
            limit_req = _parse_int(h[k])
            if limit_req is not None:
                break

    reset_req = None
    for k in (
        "x-ratelimit-reset-requests",
        "anthropic-ratelimit-requests-reset",
        "ratelimit-reset-requests",
        "x-ratelimit-reset",
        "ratelimit-reset",
        "retry-after",
    ):
        if k in h:
            reset_req = parse_reset_offset(h[k])
            if reset_req is not None:
                break

    # 2. Token Limits
    rem_tokens = None
    for k in (
        "x-ratelimit-remaining-tokens",
        "anthropic-ratelimit-tokens-remaining",
        "ratelimit-remaining-tokens",
        "x-ratelimit-remaining-tokens-input",
    ):
        if k in h:
            rem_tokens = _parse_int(h[k])
            if rem_tokens is not None:
                break

    limit_tokens = None
    for k in (
        "x-ratelimit-limit-tokens",
        "anthropic-ratelimit-tokens-limit",
        "ratelimit-limit-tokens",
    ):
        if k in h:
            limit_tokens = _parse_int(h[k])
            if limit_tokens is not None:
                break

    reset_tokens = None
    for k in (
        "x-ratelimit-reset-tokens",
        "anthropic-ratelimit-tokens-reset",
        "ratelimit-reset-tokens",
    ):
        if k in h:
            reset_tokens = parse_reset_offset(h[k])
            if reset_tokens is not None:
                break

    return RateLimitSnapshot(
        pool_name=pool_name,
        remaining_requests=rem_req,
        limit_requests=limit_req,
        reset_requests_s=reset_req,
        remaining_tokens=rem_tokens,
        limit_tokens=limit_tokens,
        reset_tokens_s=reset_tokens,
        updated_at=time.time(),
        source="header",
    )


def query_upstream_balance(plugin: Any, timeout: float = 5.0) -> dict[str, Any] | None:
    """Query provider balance/quota via upstream API where available.

    Supports:
    * OpenRouter (``GET https://openrouter.ai/api/v1/auth/key``)
    * Custom balance / check_quota implementations
    """
    if plugin is None:
        return None

    endpoint = getattr(plugin, "default_endpoint", "") or ""
    api_key = getattr(plugin, "api_key", None)

    # 1. OpenRouter
    if "openrouter.ai" in endpoint and api_key:
        try:
            import requests  # noqa: PLC0415

            resp = requests.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                usage = _parse_float(data.get("usage"))
                limit = _parse_float(data.get("limit"))
                is_free = bool(data.get("is_free_tier", False))
                balance = None
                if limit is not None and usage is not None:
                    balance = max(0.0, limit - usage)
                return {
                    "balance_usd": balance,
                    "usage_usd": usage,
                    "limit_usd": limit,
                    "is_free_tier": is_free,
                    "provider": "openrouter",
                }
        except Exception as exc:  # noqa: BLE001
            log.debug(
                "OpenRouter balance query failed for %s: %r", getattr(plugin, "name", ""), exc
            )

    # 2. General check_quota method fallback
    if hasattr(plugin, "check_quota") and callable(plugin.check_quota):
        try:
            res = plugin.check_quota()
            if isinstance(res, dict) and (
                res.get("remaining") is not None or res.get("balance") is not None
            ):
                return {
                    "remaining_tokens": _parse_int(res.get("remaining")),
                    "total_tokens": _parse_int(res.get("total")),
                    "balance_usd": _parse_float(res.get("balance")),
                    "tier": res.get("tier"),
                    "provider": getattr(plugin, "provider_id", "unknown"),
                }
        except Exception as exc:  # noqa: BLE001
            log.debug("check_quota failed for %s: %r", getattr(plugin, "name", ""), exc)

    return None


class QuotaTracker:
    """Thread-safe registry for real-time provider quota and rate-limit tracking."""

    def __init__(self) -> None:
        self._snapshots: dict[str, RateLimitSnapshot] = {}
        self._api_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def record_headers(self, pool_name: str, headers: Mapping[str, Any]) -> RateLimitSnapshot:
        """Parse headers and update the pool snapshot (merging existing values)."""
        new_snap = parse_rate_limit_headers(headers, pool_name=pool_name)
        with self._lock:
            existing = self._snapshots.get(pool_name)
            if existing is not None:
                # Merge: only override if the new snapshot actually parsed a value
                if new_snap.remaining_requests is not None:
                    existing.remaining_requests = new_snap.remaining_requests
                if new_snap.limit_requests is not None:
                    existing.limit_requests = new_snap.limit_requests
                if new_snap.reset_requests_s is not None:
                    existing.reset_requests_s = new_snap.reset_requests_s

                if new_snap.remaining_tokens is not None:
                    existing.remaining_tokens = new_snap.remaining_tokens
                if new_snap.limit_tokens is not None:
                    existing.limit_tokens = new_snap.limit_tokens
                if new_snap.reset_tokens_s is not None:
                    existing.reset_tokens_s = new_snap.reset_tokens_s

                existing.updated_at = time.time()
                existing.source = "header"
                self._check_and_emit_alert(existing)
                return existing

            self._snapshots[pool_name] = new_snap
            self._check_and_emit_alert(new_snap)
            return new_snap

    def record_api_result(self, pool_name: str, data: dict[str, Any]) -> RateLimitSnapshot:
        """Update snapshot with data from balance/quota API."""
        with self._lock:
            snap = self._snapshots.get(pool_name) or RateLimitSnapshot(pool_name=pool_name)
            if "balance_usd" in data and data["balance_usd"] is not None:
                snap.balance_usd = _parse_float(data["balance_usd"])
            if "remaining_tokens" in data and data["remaining_tokens"] is not None:
                snap.remaining_tokens = _parse_int(data["remaining_tokens"])
            if "total_tokens" in data and data["total_tokens"] is not None:
                snap.limit_tokens = _parse_int(data["total_tokens"])
            if "is_free_tier" in data:
                snap.is_free_tier = bool(data["is_free_tier"])
            snap.updated_at = time.time()
            snap.source = "api"
            self._snapshots[pool_name] = snap
            self._check_and_emit_alert(snap)
            return snap

    def _check_and_emit_alert(self, snap: RateLimitSnapshot) -> None:
        """Check if snapshot values warrant a proactive low-quota alert."""
        try:
            from core.alerting import get_alert_manager  # noqa: PLC0415

            mgr = get_alert_manager()
            if snap.remaining_requests is not None and snap.remaining_requests <= 2:
                mgr.emit(
                    pool_name=snap.pool_name,
                    event_type="low_quota",
                    level="warning",
                    message=f"Only {snap.remaining_requests} request(s) remaining (resets in {snap.reset_requests_s or '?'}s)",
                    details=snap.to_dict(),
                )
            elif snap.remaining_tokens is not None and snap.remaining_tokens <= 500:
                mgr.emit(
                    pool_name=snap.pool_name,
                    event_type="low_quota",
                    level="warning",
                    message=f"Only {snap.remaining_tokens} token(s) remaining (resets in {snap.reset_tokens_s or '?'}s)",
                    details=snap.to_dict(),
                )
            elif snap.balance_usd is not None and snap.balance_usd <= 1.0:
                mgr.emit(
                    pool_name=snap.pool_name,
                    event_type="low_quota",
                    level="warning",
                    message=f"Balance low: ${snap.balance_usd:.2f} USD remaining",
                    details=snap.to_dict(),
                )
        except Exception as exc:  # noqa: BLE001
            log.debug("Failed to emit quota alert: %r", exc)

    def get_snapshot(self, pool_name: str) -> RateLimitSnapshot | None:
        """Return current snapshot for a pool if tracked."""
        with self._lock:
            return self._snapshots.get(pool_name)

    def is_pool_exhausted(
        self,
        pool_name: str,
        token_buffer: int = DEFAULT_TOKEN_BUFFER,
        request_buffer: int = DEFAULT_REQUEST_BUFFER,
    ) -> bool:
        """Check if pool is currently exhausted based on headers or API balances."""
        with self._lock:
            snap = self._snapshots.get(pool_name)
            if snap is None:
                return False
            return snap.is_exhausted(token_buffer=token_buffer, request_buffer=request_buffer)

    def query_pool_quota(
        self,
        plugin: Any,
        cache_ttl: float = DEFAULT_API_CACHE_TTL,
        force: bool = False,
    ) -> dict[str, Any] | None:
        """Query upstream balance with caching to avoid rate-limiting the quota endpoint."""
        name = getattr(plugin, "name", "")
        now = time.time()
        with self._lock:
            cached = self._api_cache.get(name)
            if not force and cached is not None:
                ts, data = cached
                if (now - ts) < cache_ttl:
                    return data

        res = query_upstream_balance(plugin)
        if res is not None:
            with self._lock:
                self._api_cache[name] = (now, res)
                self.record_api_result(name, res)
        return res

    def all_snapshots(self) -> dict[str, dict[str, Any]]:
        """Return dict representation of all active snapshots."""
        with self._lock:
            return {name: snap.to_dict() for name, snap in self._snapshots.items()}

    def clear(self) -> None:
        """Clear all snapshots and caches (primarily for unit testing)."""
        with self._lock:
            self._snapshots.clear()
            self._api_cache.clear()


_TRACKER: QuotaTracker | None = None
_TRACKER_LOCK = threading.Lock()


def get_quota_tracker() -> QuotaTracker:
    """Return the global QuotaTracker singleton."""
    global _TRACKER
    if _TRACKER is None:
        with _TRACKER_LOCK:
            if _TRACKER is None:
                _TRACKER = QuotaTracker()
    return _TRACKER


__all__ = [
    "QuotaTracker",
    "RateLimitSnapshot",
    "get_quota_tracker",
    "parse_rate_limit_headers",
    "parse_reset_offset",
    "query_upstream_balance",
    "DEFAULT_TOKEN_BUFFER",
    "DEFAULT_REQUEST_BUFFER",
]
