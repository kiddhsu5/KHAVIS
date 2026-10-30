"""Quota-aware pool health tracking and circuit breaking (v0.2.0).

This module is what makes the "zero quota interruption" promise real. Every
provider plugin already exposes :meth:`ProviderPlugin.health_check` and
:meth:`ProviderPlugin.check_quota`; before this module existed nothing in
``core/`` ever called them, so the router happily handed requests to a pool
that had been returning 429 for ten minutes.

Design notes
------------
**Cached state, never inline I/O.** :meth:`CapabilityRouter.select` must stay
fast and must not block the event loop, so it only *reads* cached state. State
is refreshed out-of-band via :meth:`PoolHealthTracker.refresh_all` (startup,
cron, ``khavis doctor``) or updated inline by the request path via
``record_success`` / ``record_failure``.

**Exhausted is not broken.** A pool whose quota is spent is perfectly healthy —
it simply has nothing left to give. That is a separate state
(:attr:`PoolState.EXHAUSTED`) from a circuit that has *tripped*
(:attr:`PoolState.OPEN`), because the two recover differently: exhaustion
clears on a successful call, a quota refresh reporting remaining > 0, or a
single probe after ``exhausted_cooldown_s``; a tripped circuit only after a
cooldown, via exactly one ``HALF_OPEN`` probe at a time.

**Optimistic on cold start.** A pool that has never been checked is
``UNKNOWN`` and is treated as available. Otherwise a fresh process would have
zero candidates and refuse to route anything.

Policy is YAML-first (``config/health.yaml``) with env overrides.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Collection, Iterable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_POLICY: dict[str, Any] = {
    # Consecutive failures before the circuit trips.
    "failure_threshold": 3,
    # Seconds an open circuit stays closed to traffic before a probe is allowed.
    "cooldown_s": 60.0,
    # Consecutive successes in half-open needed to fully close again.
    "success_threshold": 1,
    # Honour check_quota() == 0 as "do not route here".
    "respect_quota": True,
    # Apply availability filtering inside CapabilityRouter.select().
    "filter_router": True,
    # What to do when every candidate is unavailable.
    #   "fail"    -> select() returns None, caller raises a clear error
    #   "degrade" -> filter_available()/select() fall back to the original
    #                candidate list (YAML order) so the caller still tries
    "on_all_unavailable": "fail",
    # Whether a tripped circuit is retried at all when the cooldown has passed.
    "allow_half_open": True,
    # Seconds an EXHAUSTED pool waits before one probe is let through again
    # (quota windows reset even when nobody runs a quota refresh). 0 = only
    # a successful call / quota refresh clears exhaustion.
    "exhausted_cooldown_s": 300.0,
}

_ON_ALL_UNAVAILABLE = ("fail", "degrade")


class PoolState(StrEnum):
    """Lifecycle state of a single pool."""

    #: Never checked yet — optimistic, treated as available.
    UNKNOWN = "unknown"
    #: Healthy, traffic flows.
    CLOSED = "closed"
    #: Circuit tripped. Not available until the cooldown elapses.
    OPEN = "open"
    #: Cooldown elapsed, one probe allowed through.
    HALF_OPEN = "half_open"
    #: Quota reported as spent. Recovers on the next quota refresh.
    EXHAUSTED = "exhausted"


@dataclass
class HealthPolicy:
    failure_threshold: int = 3
    cooldown_s: float = 60.0
    success_threshold: int = 1
    respect_quota: bool = True
    filter_router: bool = True
    on_all_unavailable: str = "fail"
    allow_half_open: bool = True
    exhausted_cooldown_s: float = 300.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HealthPolicy:
        merged = {**_DEFAULT_POLICY, **(data or {})}

        def _int(key: str) -> int:
            try:
                return int(merged[key])
            except (TypeError, ValueError):
                return int(_DEFAULT_POLICY[key])

        def _float(key: str) -> float:
            try:
                return float(merged[key])
            except (TypeError, ValueError):
                return float(_DEFAULT_POLICY[key])

        on_all = str(merged.get("on_all_unavailable", "fail")).strip().lower()
        if on_all not in _ON_ALL_UNAVAILABLE:
            on_all = str(_DEFAULT_POLICY["on_all_unavailable"])

        return cls(
            failure_threshold=max(1, _int("failure_threshold")),
            cooldown_s=max(0.0, _float("cooldown_s")),
            success_threshold=max(1, _int("success_threshold")),
            respect_quota=bool(merged.get("respect_quota", True)),
            filter_router=bool(merged.get("filter_router", True)),
            on_all_unavailable=on_all,
            allow_half_open=bool(merged.get("allow_half_open", True)),
            exhausted_cooldown_s=max(0.0, _float("exhausted_cooldown_s")),
        )


def load_policy(path: Path | str | None = None) -> HealthPolicy:
    """Load the health policy from ``config/health.yaml`` with env overrides."""

    env_map = {
        "KHAVIS_HEALTH_FAILURE_THRESHOLD": ("failure_threshold", int),
        "KHAVIS_HEALTH_COOLDOWN_S": ("cooldown_s", float),
        "KHAVIS_HEALTH_SUCCESS_THRESHOLD": ("success_threshold", int),
        "KHAVIS_HEALTH_RESPECT_QUOTA": ("respect_quota", bool),
        "KHAVIS_HEALTH_FILTER_ROUTER": ("filter_router", bool),
        "KHAVIS_HEALTH_ALLOW_HALF_OPEN": ("allow_half_open", bool),
        "KHAVIS_HEALTH_ON_ALL_UNAVAILABLE": ("on_all_unavailable", str),
        "KHAVIS_HEALTH_EXHAUSTED_COOLDOWN_S": ("exhausted_cooldown_s", float),
    }
    overrides: dict[str, Any] = {}

    def _as_bool(raw: str) -> bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    for env_name, (key, caster) in env_map.items():
        raw = os.getenv(env_name)
        if raw is None or raw == "":
            continue
        try:
            overrides[key] = _as_bool(raw) if caster is bool else caster(raw)
        except (TypeError, ValueError):
            continue

    cfg: dict[str, Any] = {}
    if path is None:
        from .env import config_dir  # noqa: PLC0415 - avoid import at module load

        default = config_dir() / "health.yaml"
        if default.exists():
            path = default
    if path is not None:
        p = Path(path)
        if p.exists():
            try:
                cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                cfg = {}
            if isinstance(cfg, dict):
                cfg = cfg.get("health", cfg) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.update(overrides)
    return HealthPolicy.from_dict(cfg)


@dataclass
class PoolHealth:
    """Cached health + quota snapshot for one pool."""

    name: str
    state: PoolState = PoolState.UNKNOWN
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    opened_at: float | None = None
    last_error: str | None = None
    last_success_at: float | None = None
    last_checked: float | None = None
    quota_remaining: int | None = None
    quota_total: int | None = None
    quota_tier: str | None = None
    #: Monotonic time the pool entered ``EXHAUSTED`` (drives time-based recovery).
    exhausted_at: float | None = None
    #: Monotonic time the current ``HALF_OPEN`` probe was handed out, or
    #: ``None`` when no probe is in flight.
    probe_started_at: float | None = None

    def cooldown_elapsed(self, now: float, cooldown_s: float) -> bool:
        """True when an ``OPEN`` circuit has waited long enough to probe."""
        if self.state is not PoolState.OPEN or self.opened_at is None:
            return True
        return (now - self.opened_at) >= cooldown_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool": self.name,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_checked": self.last_checked,
            "quota_remaining": self.quota_remaining,
            "quota_total": self.quota_total,
            "quota_tier": self.quota_tier,
            "probe_in_flight": self.probe_started_at is not None,
        }


# Explicit quota / rate-limit phrases. A bare "quota" or "429" substring is
# far too loose ("quota_project_id", "port 14290", "request 4291 failed"), so
# every marker is anchored on word boundaries.
_QUOTA_MESSAGE_RE = re.compile(
    r"\binsufficient[_ ]quota\b"
    r"|\bexceeded your current quota\b"
    r"|\bresource[_ ]exhausted\b"
    r"|\brate[_ -]?limit"  # rate limit / rate_limit / rate-limited / ratelimit
    r"|\btoo many requests\b"
    r"|\bbilling hard limit\b",
    re.IGNORECASE,
)
# A bare "429" only means quota when nothing better is available — see
# ``_looks_like_quota_error``, which lets this pattern speak only when no
# structured status code was supplied.
_STATUS_429_RE = re.compile(r"\b429\b")
# Floor for ``PoolHealthTracker.probe_timeout_s``. See that property: binding it
# to a zero cooldown would disable the single-probe guarantee entirely.
_MIN_PROBE_TIMEOUT_S = 1.0


def _status_of(error: object) -> int | None:
    """Best-effort HTTP status extraction from an SDK / requests exception."""
    if error is None or isinstance(error, str):
        return None
    candidates: list[Any] = [
        getattr(error, "status_code", None),
        getattr(error, "http_status", None),
        getattr(error, "status", None),
        getattr(error, "code", None),
    ]
    response = getattr(error, "response", None)
    if response is not None:
        candidates.append(getattr(response, "status_code", None))
        candidates.append(getattr(response, "status", None))
    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and 100 <= value <= 599:
            return value
        if isinstance(value, str) and value.isdigit() and 100 <= int(value) <= 599:
            return int(value)
    return None


def _redact(text: str) -> str:
    from .audit import redact_secrets  # noqa: PLC0415 - keep module import light

    return redact_secrets(text)


class PoolHealthTracker:
    """Thread-safe circuit breaker + quota cache for every registered pool."""

    def __init__(self, policy: HealthPolicy | None = None) -> None:
        self.policy = policy or HealthPolicy()
        self._pools: dict[str, PoolHealth] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def get(self, name: str) -> PoolHealth:
        with self._lock:
            if name not in self._pools:
                self._pools[name] = PoolHealth(name=name)
            return self._pools[name]

    def known(self) -> Iterable[str]:
        with self._lock:
            return tuple(self._pools)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: hp.to_dict() for name, hp in self._pools.items()}

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    @property
    def probe_timeout_s(self) -> float:
        """How long a half-open probe may stay unanswered before another is allowed.

        Deliberately *not* ``cooldown_s``. That value may legitimately be ``0``
        (``KHAVIS_HEALTH_COOLDOWN_S=0``, or ``max(0.0, ...)`` on a hand-edited
        config meaning "retry immediately"), and reusing it here would make the
        guard ``(now - probe_started_at) < 0.0`` — permanently false, so every
        concurrent caller would sail into ``HALF_OPEN`` at once. That is
        exactly the stampede this class exists to prevent, and it would come
        back the moment anyone tuned the cooldown. Keep an independent floor.
        """
        return max(self.policy.cooldown_s, _MIN_PROBE_TIMEOUT_S)

    def is_available(
        self,
        name: str,
        now: float | None = None,
        *,
        claim_probe: bool = True,
    ) -> bool:
        """Whether traffic may currently be routed to ``name``.

        Unknown pools are available (cold-start optimism). ``OPEN`` circuits
        (and ``EXHAUSTED`` pools, after ``exhausted_cooldown_s``) are
        unavailable until their cooldown elapses; then exactly **one** probe
        is let through as ``HALF_OPEN``. Until that probe reports back via
        :meth:`record_success` / :meth:`record_failure` every other caller
        gets ``False``. A probe that never reports back (crashed caller)
        stops blocking after :attr:`probe_timeout_s`.

        ``claim_probe=False`` answers "would a probe be allowed?" without
        handing one out — used by :meth:`filter_available` so that merely
        listing candidates does not burn the probe slot of pools the caller
        ends up not choosing.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            hp = self.get(name)
            state = hp.state
            if state in (PoolState.UNKNOWN, PoolState.CLOSED):
                if self.policy.respect_quota:
                    try:
                        from .quota_tracker import get_quota_tracker

                        if get_quota_tracker().is_pool_exhausted(name):
                            return False
                    except Exception:  # noqa: BLE001
                        pass
                return True
            if state is PoolState.EXHAUSTED:
                cooldown = self.policy.exhausted_cooldown_s
                if cooldown <= 0 or hp.exhausted_at is None or (now - hp.exhausted_at) < cooldown:
                    return False
                return self._grant_probe(hp, now, claim_probe)
            if state is PoolState.OPEN:
                if not self.policy.allow_half_open:
                    return False
                if not hp.cooldown_elapsed(now, self.policy.cooldown_s):
                    return False
                return self._grant_probe(hp, now, claim_probe)
            # HALF_OPEN: only one probe in flight at a time.
            if (
                hp.probe_started_at is not None
                and (now - hp.probe_started_at) < self.probe_timeout_s
            ):
                return False
            return self._grant_probe(hp, now, claim_probe)

    def _grant_probe(self, hp: PoolHealth, now: float, claim: bool) -> bool:
        """Move ``hp`` to HALF_OPEN and mark a probe in flight (caller holds lock)."""
        if not claim:
            return True
        if hp.state is not PoolState.HALF_OPEN:
            hp.state = PoolState.HALF_OPEN
            hp.consecutive_successes = 0
        hp.probe_started_at = now
        return True

    def filter_available(
        self,
        names: Collection[str],
        *,
        exclude: Collection[str] = (),
        degrade: bool | None = None,
    ) -> list[str]:
        """Return the subset of ``names`` that may receive traffic, in order.

        This never claims a half-open probe: the caller must call
        :meth:`is_available` (default ``claim_probe=True``) on the pool it
        actually picks.

        When nothing is available and the policy says
        ``on_all_unavailable: degrade`` (or ``degrade=True`` is passed), the
        original non-excluded candidates are returned so the caller still
        tries them; with ``"fail"`` an empty list is returned.
        """
        skip = set(exclude)
        now = time.monotonic()
        remaining = [n for n in names if n not in skip]
        kept = [n for n in remaining if self.is_available(n, now, claim_probe=False)]
        if kept:
            return kept
        if degrade is None:
            degrade = self.policy.on_all_unavailable == "degrade"
        return remaining if degrade else []

    def explain(self, name: str) -> str:
        """Human-readable reason a pool is (un)available, for error messages."""
        now = time.monotonic()
        with self._lock:
            hp = self.get(name)
            if hp.state is PoolState.EXHAUSTED:
                retry = ""
                if self.policy.exhausted_cooldown_s > 0 and hp.exhausted_at is not None:
                    left = max(0.0, self.policy.exhausted_cooldown_s - (now - hp.exhausted_at))
                    retry = f", retry in {left:.0f}s"
                return f"{name}: quota exhausted (remaining={hp.quota_remaining}{retry})"
            if hp.state is PoolState.OPEN:
                waited = (now - hp.opened_at) if hp.opened_at else 0.0
                return (
                    f"{name}: circuit open after {hp.consecutive_failures} failures "
                    f"({waited:.0f}s / {self.policy.cooldown_s:.0f}s cooldown), "
                    f"last error: {hp.last_error}"
                )
            if hp.state is PoolState.HALF_OPEN:
                if hp.probe_started_at is not None:
                    return f"{name}: half-open, probe in flight"
                return f"{name}: half-open, awaiting probe"
            return f"{name}: {hp.state.value}"

    # ------------------------------------------------------------------
    # Request-path recording
    # ------------------------------------------------------------------
    def record_success(self, name: str) -> None:
        """Mark a successful call. Closes tripped circuits and clears exhaustion."""
        with self._lock:
            hp = self.get(name)
            hp.consecutive_failures = 0
            hp.consecutive_successes += 1
            hp.last_error = None
            hp.last_success_at = time.time()
            hp.probe_started_at = None
            if hp.state is not PoolState.HALF_OPEN:
                hp.consecutive_successes = 0
            if hp.state in (PoolState.HALF_OPEN, PoolState.OPEN):
                if hp.consecutive_successes >= self.policy.success_threshold:
                    hp.state = PoolState.CLOSED
                    hp.opened_at = None
                    hp.exhausted_at = None
                    hp.consecutive_successes = 0
            elif hp.state is PoolState.EXHAUSTED:
                # A successful call means quota is back (top-up or window reset).
                hp.state = PoolState.CLOSED
                hp.exhausted_at = None
            elif hp.state is PoolState.UNKNOWN:
                hp.state = PoolState.CLOSED

    def record_failure(
        self,
        name: str,
        error: str | BaseException | None = None,
        *,
        status_code: int | None = None,
    ) -> None:
        """Mark a failed call. Trips the circuit once the threshold is hit.

        Quota / rate-limit errors are recognised (HTTP 429 via ``status_code``
        or the exception's ``status_code`` / ``status`` / ``response``
        attributes first, explicit message phrases second) and routed to
        ``EXHAUSTED`` instead of tripping the circuit — a spent quota is not
        a broken provider.

        Any failure while ``HALF_OPEN`` (the probe failed) re-opens the
        circuit immediately and restarts the cooldown.
        """
        raw = str(error) if error is not None else "unknown error"
        quota = self._looks_like_quota_error(error if error is not None else raw, status_code)
        now = time.monotonic()
        with self._lock:
            hp = self.get(name)
            # Attempt to extract response headers if available on error
            resp_obj = getattr(error, "response", None)
            err_headers = getattr(resp_obj, "headers", None) if resp_obj is not None else None
            if err_headers:
                try:
                    from .quota_tracker import get_quota_tracker

                    get_quota_tracker().record_headers(name, err_headers)
                except Exception:  # noqa: BLE001
                    pass
            was_half_open = hp.state is PoolState.HALF_OPEN
            hp.consecutive_failures += 1
            hp.consecutive_successes = 0
            hp.last_error = _redact(raw)
            hp.last_checked = time.time()
            hp.probe_started_at = None

            if quota:
                hp.state = PoolState.EXHAUSTED
                hp.exhausted_at = now
                hp.quota_remaining = 0
                try:
                    from .alerting import get_alert_manager  # noqa: PLC0415

                    get_alert_manager().emit(
                        pool_name=name,
                        event_type="rate_limit_429",
                        level="error",
                        message=f"Rate limit / 429 quota exhaustion detected: {_redact(raw)[:120]}",
                        details={"failures": hp.consecutive_failures},
                    )
                except Exception:  # noqa: BLE001
                    pass
                return

            if was_half_open or hp.consecutive_failures >= self.policy.failure_threshold:
                hp.state = PoolState.OPEN
                hp.opened_at = now
                try:
                    from .alerting import get_alert_manager  # noqa: PLC0415

                    get_alert_manager().emit(
                        pool_name=name,
                        event_type="circuit_open",
                        level="critical",
                        message=f"Circuit breaker tripped OPEN after {hp.consecutive_failures} failures: {_redact(raw)[:120]}",
                        details={"failures": hp.consecutive_failures},
                    )
                except Exception:  # noqa: BLE001
                    pass
            elif hp.state is not PoolState.OPEN:
                hp.state = PoolState.CLOSED

    @staticmethod
    def _looks_like_quota_error(
        error: str | BaseException | None, status_code: int | None = None
    ) -> bool:
        """Classify ``error`` as quota / rate-limit exhaustion.

        A structured HTTP status is authoritative and the message cannot
        overrule it. This matters more than it looks: ``EXHAUSTED`` is only
        left by a *successful* call or a quota refresh, so anything routed
        into it that can never succeed poisons the pool for the whole
        ``exhausted_cooldown_s`` with no self-healing path. A 400 that merely
        says "your rate limit config is malformed", or a 500 that says "too
        many requests in queue", is a broken *request*, not a spent *account* —
        those belong in the circuit breaker, which does time out and recover.

        With no structured status (a raw transport/SDK exception, or a bare
        "429" string) the message is the only evidence available, so the
        phrase match still applies.
        """
        status = status_code if status_code is not None else _status_of(error)
        if status is not None:
            return status == 429
        message = "" if error is None else str(error)
        return bool(_QUOTA_MESSAGE_RE.search(message) or _STATUS_429_RE.search(message))

    # ------------------------------------------------------------------
    # Out-of-band refresh (never called from select())
    # ------------------------------------------------------------------
    def refresh(self, plugin: Any, now: float | None = None) -> PoolHealth:
        """Probe one plugin's ``health_check`` + ``check_quota`` and cache it.

        Network I/O runs *outside* the tracker lock; the lock is only held
        while the result is written back.

        A passing ``health_check`` is weak evidence (most plugins only check
        that an API key is configured), so refresh never closes an ``OPEN``
        / ``HALF_OPEN`` circuit nor resets its failure streak — only real
        traffic (``record_success``) or the cooldown probe does. Refresh
        is what moves ``UNKNOWN`` pools to ``CLOSED`` and keeps the quota
        snapshot (``EXHAUSTED``) current.

        Never raises: a provider that cannot answer a health probe is recorded
        as unavailable with the exception text, not propagated to the caller.
        """
        name = getattr(plugin, "name", None) or type(plugin).__name__

        health: Any = None
        health_exc: BaseException | None = None
        try:
            health = plugin.health_check()
        except Exception as exc:  # noqa: BLE001 - provider code is untrusted
            health_exc = exc

        ok = health_exc is None and (
            bool(health.get("ok")) if isinstance(health, dict) else bool(health)
        )
        quota: Any = None
        if ok and self.policy.respect_quota:
            try:
                quota = plugin.check_quota()
            except Exception:  # noqa: BLE001 - quota is best-effort
                quota = None

        now = time.monotonic() if now is None else now
        with self._lock:
            hp = self.get(name)
            hp.last_checked = time.time()

            if health_exc is not None:
                hp.state = PoolState.OPEN
                hp.opened_at = now
                hp.probe_started_at = None
                hp.consecutive_failures = max(
                    hp.consecutive_failures, self.policy.failure_threshold
                )
                hp.last_error = _redact(f"health_check raised: {health_exc!r}")
                return hp

            if not ok:
                detail = health.get("detail") if isinstance(health, dict) else None
                hp.consecutive_failures += 1
                hp.last_error = _redact(f"unhealthy: {detail}" if detail else "unhealthy")
                if (
                    hp.state is PoolState.HALF_OPEN
                    or hp.consecutive_failures >= self.policy.failure_threshold
                ):
                    hp.state = PoolState.OPEN
                    hp.opened_at = now
                    hp.probe_started_at = None
                return hp

            if isinstance(quota, dict):
                hp.quota_remaining = _as_int(quota.get("remaining"))
                hp.quota_total = _as_int(quota.get("total"))
                tier = quota.get("tier")
                hp.quota_tier = str(tier) if tier is not None else None

            if hp.state in (PoolState.OPEN, PoolState.HALF_OPEN):
                # A tripped circuit only recovers through a real probe.
                return hp

            if isinstance(quota, dict) and hp.quota_remaining == 0:
                if hp.state is not PoolState.EXHAUSTED:
                    hp.state = PoolState.EXHAUSTED
                    hp.exhausted_at = now
                return hp

            if hp.state is PoolState.EXHAUSTED:
                # Only positive quota evidence clears exhaustion here; otherwise
                # wait for a successful call or the exhausted cooldown probe.
                if hp.quota_remaining is None or hp.quota_remaining <= 0:
                    return hp
                hp.exhausted_at = None

            hp.state = PoolState.CLOSED
            hp.consecutive_failures = 0
            hp.opened_at = None
            hp.last_error = None
            return hp

    def refresh_all(
        self, plugins: Iterable[Any], now: float | None = None
    ) -> dict[str, dict[str, Any]]:
        """Refresh every plugin and return the full snapshot."""
        for plugin in plugins:
            # A plugin that explodes must not abort the sweep.
            with suppress(Exception):
                self.refresh(plugin, now=now)
        return self.snapshot()

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def reset(self, name: str | None = None) -> None:
        """Clear cached state for one pool, or all of them."""
        with self._lock:
            if name is None:
                self._pools.clear()
            else:
                self._pools.pop(name, None)

    def force_close(self, name: str) -> None:
        """Manually close a circuit (operator override / ``khavis health --reset``)."""
        with self._lock:
            hp = self.get(name)
            hp.state = PoolState.CLOSED
            hp.consecutive_failures = 0
            hp.consecutive_successes = 0
            hp.opened_at = None
            hp.exhausted_at = None
            hp.probe_started_at = None
            hp.last_error = None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_TRACKER: PoolHealthTracker | None = None
_TRACKER_LOCK = threading.Lock()


def get_tracker(policy: HealthPolicy | None = None) -> PoolHealthTracker:
    """Return the process-wide tracker, creating it on first use."""
    global _TRACKER
    with _TRACKER_LOCK:
        if _TRACKER is None:
            _TRACKER = PoolHealthTracker(policy or load_policy())
        return _TRACKER


def reset_tracker(policy: HealthPolicy | None = None) -> PoolHealthTracker:
    """Replace the process-wide tracker. Intended for tests."""
    global _TRACKER
    with _TRACKER_LOCK:
        _TRACKER = PoolHealthTracker(policy or HealthPolicy())
        return _TRACKER


__all__ = [
    "HealthPolicy",
    "PoolHealth",
    "PoolHealthTracker",
    "PoolState",
    "get_tracker",
    "load_policy",
    "reset_tracker",
]
