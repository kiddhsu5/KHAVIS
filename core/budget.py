"""Token-aware cost budgets (v0.2.0).

Tracks per-request and per-session token / USD spend, blocks the next
call once a cap is hit, and exposes a status dict for the dashboard.

Typical use::

    from core.budget import get_guard

    guard = get_guard()
    ok, reason, reservation = guard.reserve(
        session_id="chat-1", pool="MiMo-Code", prompt_chars=800
    )
    if not ok:
        raise RuntimeError(reason)
    result = plugin.chat(...)
    guard.record(
        session_id="chat-1", pool="MiMo-Code", usage=result.get("usage"),
        reservation=reservation,
    )

``reserve`` checks the caps and books the estimate under one lock, so N
concurrent requests cannot all pass the same pre-flight check and overshoot
the session cap together. ``record`` replaces the reservation with the
actual usage; an unsettled reservation (caller crashed / errored without
calling ``record``/``release``) expires after ``RESERVATION_TTL_S``.

``check`` is the side-effect-free variant returning ``(ok, reason)``.
``allow`` / ``reason`` are kept for backwards compatibility, but
``reason()`` is shared guard state — under concurrency it may describe a
different request, so new code should use ``check`` / ``reserve``.

Policy is YAML-first (``config/budgets.yaml``) with env overrides.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_POLICY: dict[str, Any] = {
    "max_tokens_per_request": 32_000,
    "max_tokens_per_session": 400_000,
    "max_cost_per_session_usd": 5.0,
    "window_s": 3600.0,
    "filter_router": True,
}

#: Seconds an unsettled reservation keeps counting against the session.
RESERVATION_TTL_S = 300.0


@dataclass
class BudgetPolicy:
    max_tokens_per_request: int = 32_000
    max_tokens_per_session: int = 400_000
    max_cost_per_session_usd: float = 5.0
    window_s: float = 3600.0
    filter_router: bool = True
    pricing_usd_per_1k: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BudgetPolicy:
        pol = dict(_DEFAULT_POLICY)
        pol.update(data.get("policy") or {})
        pricing = {str(k): float(v) for k, v in (data.get("pricing_usd_per_1k") or {}).items()}
        return cls(
            max_tokens_per_request=int(pol["max_tokens_per_request"]),
            max_tokens_per_session=int(pol["max_tokens_per_session"]),
            max_cost_per_session_usd=float(pol["max_cost_per_session_usd"]),
            window_s=float(pol["window_s"]),
            filter_router=bool(pol["filter_router"]),
            pricing_usd_per_1k=pricing,
        )


def load_policy(path: Path | str | None = None) -> BudgetPolicy:
    """Load ``config/budgets.yaml``; apply env overrides."""
    if path is None:
        from .env import config_dir  # noqa: PLC0415 - avoid import at module load

        path = config_dir() / "budgets.yaml"
    data: dict[str, Any] = {}
    p = Path(path)
    if p.exists():
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            data = {}
    policy = BudgetPolicy.from_dict(data)

    def _env_int(name: str, cur: int) -> int:
        raw = os.environ.get(name)
        return int(raw) if raw and raw.isdigit() else cur

    def _env_float(name: str, cur: float) -> float:
        raw = os.environ.get(name)
        try:
            return float(raw) if raw else cur
        except ValueError:
            return cur

    policy.max_tokens_per_request = _env_int(
        "KHAVIS_BUDGET_MAX_TOKENS_REQUEST", policy.max_tokens_per_request
    )
    policy.max_tokens_per_session = _env_int(
        "KHAVIS_BUDGET_MAX_TOKENS_SESSION", policy.max_tokens_per_session
    )
    policy.max_cost_per_session_usd = _env_float(
        "KHAVIS_BUDGET_MAX_COST_SESSION_USD", policy.max_cost_per_session_usd
    )
    policy.window_s = _env_float("KHAVIS_BUDGET_WINDOW_S", policy.window_s)
    return policy


def estimate_tokens(text: str | None) -> int:
    """Cheap char/4 heuristic — good enough for pre-flight caps."""
    if not text:
        return 0
    return estimate_tokens_from_chars(len(text))


def estimate_tokens_from_chars(chars: int) -> int:
    """Same char/4 heuristic, from a length (no string allocation)."""
    chars = int(chars or 0)
    if chars <= 0:
        return 0
    return max(1, chars // 4)


@dataclass
class _Event:
    ts: float
    tokens: int
    cost_usd: float
    pool: str
    #: Set for pre-flight reservations; ``None`` for settled usage.
    reservation: str | None = None
    #: Monotonic deadline after which an unsettled reservation is dropped.
    expires: float | None = None


class BudgetGuard:
    """Sliding-window session budget + per-request cap."""

    def __init__(self, policy: BudgetPolicy | None = None) -> None:
        self.policy = policy or load_policy()
        self._lock = threading.Lock()
        self._sessions: dict[str, deque[_Event]] = {}
        self._last_reason = ""

    # ------------------------------------------------------------------
    def reason(self) -> str:
        """Reason of the last denied :meth:`allow` call.

        Deprecated: shared across requests/threads. Use :meth:`check` or
        :meth:`reserve`, which return the reason alongside the decision.
        """
        return self._last_reason

    def _window(self, session_id: str) -> deque[_Event]:
        q = self._sessions.setdefault(session_id, deque())
        cutoff = time.time() - self.policy.window_s
        while q and q[0].ts < cutoff:
            q.popleft()
        now = time.monotonic()
        if any(e.expires is not None and e.expires < now for e in q):
            kept = [e for e in q if e.expires is None or e.expires >= now]
            q.clear()
            q.extend(kept)
        return q

    def _session_usage_unlocked(self, session_id: str) -> dict[str, float]:
        q = self._window(session_id)
        return {
            "tokens": float(sum(e.tokens for e in q)),
            "cost_usd": round(sum(e.cost_usd for e in q), 6),
            "calls": float(len(q)),
        }

    def session_usage(self, session_id: str) -> dict[str, float]:
        with self._lock:
            return self._session_usage_unlocked(session_id)

    def pool_usage(self, session_id: str | None = None) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        with self._lock:
            for sid, q in self._sessions.items():
                if session_id and sid != session_id:
                    continue
                self._window(sid)  # prune
                for e in q:
                    row = out.setdefault(e.pool, {"tokens": 0.0, "cost_usd": 0.0, "calls": 0.0})
                    row["tokens"] += e.tokens
                    row["cost_usd"] += e.cost_usd
                    row["calls"] += 1
        return out

    def cost_of(self, pool: str, tokens: int) -> float:
        rate = self.policy.pricing_usd_per_1k.get(pool, 0.0)
        return rate * tokens / 1000.0

    # ------------------------------------------------------------------
    def _evaluate_unlocked(self, session_id: str, pool: str, est: int) -> tuple[bool, str]:
        pol = self.policy
        if est > pol.max_tokens_per_request:
            return False, (
                f"request estimate {est} tokens exceeds max_tokens_per_request="
                f"{pol.max_tokens_per_request}"
            )
        usage = self._session_usage_unlocked(session_id)
        if usage["tokens"] + est > pol.max_tokens_per_session:
            return False, (
                f"session {session_id!r} at {int(usage['tokens'])} tokens; "
                f"cap max_tokens_per_session={pol.max_tokens_per_session}"
            )
        if pol.max_cost_per_session_usd > 0:
            est_cost = self.cost_of(pool, est)
            projected = usage["cost_usd"] + est_cost
            if projected > pol.max_cost_per_session_usd:
                return False, (
                    f"session cost ${usage['cost_usd']:.4f} + est ${est_cost:.4f} "
                    f"would exceed max_cost_per_session_usd={pol.max_cost_per_session_usd}"
                )
        return True, ""

    @staticmethod
    def _estimate(prompt_chars: int, estimated_completion_tokens: int) -> int:
        return estimate_tokens_from_chars(prompt_chars) + max(0, int(estimated_completion_tokens))

    def check(
        self,
        *,
        session_id: str = "default",
        pool: str = "",
        prompt_chars: int = 0,
        estimated_completion_tokens: int = 0,
    ) -> tuple[bool, str]:
        """Side-effect-free pre-flight check. Returns ``(ok, reason)``."""
        est = self._estimate(prompt_chars, estimated_completion_tokens)
        with self._lock:
            return self._evaluate_unlocked(session_id, pool, est)

    def reserve(
        self,
        *,
        session_id: str = "default",
        pool: str = "",
        prompt_chars: int = 0,
        estimated_completion_tokens: int = 0,
    ) -> tuple[bool, str, str | None]:
        """Atomically check the caps and book the estimate against the session.

        Returns ``(ok, reason, reservation_id)``. Pass ``reservation_id`` to
        :meth:`record` (settles it with actual usage) or :meth:`release`
        (drops it, e.g. when the upstream call failed). Unsettled
        reservations expire after :data:`RESERVATION_TTL_S`.
        """
        est = self._estimate(prompt_chars, estimated_completion_tokens)
        with self._lock:
            ok, reason = self._evaluate_unlocked(session_id, pool, est)
            if not ok:
                return False, reason, None
            if est <= 0:
                return True, "", None
            rid = uuid.uuid4().hex
            self._window(session_id).append(
                _Event(
                    ts=time.time(),
                    tokens=est,
                    cost_usd=self.cost_of(pool, est),
                    pool=pool or "?",
                    reservation=rid,
                    expires=time.monotonic() + RESERVATION_TTL_S,
                )
            )
            return True, "", rid

    def _drop_reservation_unlocked(self, session_id: str, reservation: str) -> bool:
        q = self._sessions.get(session_id)
        if not q:
            return False
        for ev in q:
            if ev.reservation == reservation:
                q.remove(ev)
                return True
        return False

    def release(self, reservation: str | None, *, session_id: str = "default") -> bool:
        """Drop an unsettled reservation. Returns ``True`` if one was removed."""
        if not reservation:
            return False
        with self._lock:
            return self._drop_reservation_unlocked(session_id, reservation)

    def allow(
        self,
        *,
        session_id: str = "default",
        pool: str = "",
        prompt_chars: int = 0,
        estimated_completion_tokens: int = 0,
    ) -> bool:
        """Pre-flight check (compat wrapper over :meth:`check`).

        Sets :meth:`reason` when denying; prefer :meth:`check` /
        :meth:`reserve` which return the reason without shared state.
        """
        ok, reason = self.check(
            session_id=session_id,
            pool=pool,
            prompt_chars=prompt_chars,
            estimated_completion_tokens=estimated_completion_tokens,
        )
        self._last_reason = reason
        return ok

    def record(
        self,
        *,
        session_id: str = "default",
        pool: str = "",
        usage: dict[str, Any] | None = None,
        reservation: str | None = None,
    ) -> dict[str, float]:
        """Record actual usage after a successful (or failed) chat().

        When ``reservation`` (from :meth:`reserve`) is given, the booked
        estimate is replaced by the actual usage.
        """
        usage = usage or {}
        try:
            tokens = int((usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0))
            if tokens <= 0:
                tokens = int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            tokens = 0
        cost = self.cost_of(pool, tokens)
        ev = _Event(ts=time.time(), tokens=tokens, cost_usd=cost, pool=pool or "?")
        with self._lock:
            if reservation:
                self._drop_reservation_unlocked(session_id, reservation)
            self._window(session_id).append(ev)
            # drop sessions with no recent events to bound memory
            cutoff = time.time() - self.policy.window_s
            for sid in list(self._sessions):
                q = self._sessions[sid]
                while q and q[0].ts < cutoff:
                    q.popleft()
                if not q:
                    del self._sessions[sid]
        return {"tokens": float(tokens), "cost_usd": cost}

    def status(self) -> dict[str, Any]:
        pol = self.policy
        return {
            "policy": {
                "max_tokens_per_request": pol.max_tokens_per_request,
                "max_tokens_per_session": pol.max_tokens_per_session,
                "max_cost_per_session_usd": pol.max_cost_per_session_usd,
                "window_s": pol.window_s,
                "filter_router": pol.filter_router,
            },
            "sessions": {sid: self.session_usage(sid) for sid in list(self._sessions)},
            "by_pool": self.pool_usage(),
        }


_GUARD: BudgetGuard | None = None
_GUARD_LOCK = threading.Lock()


def get_guard() -> BudgetGuard:
    """Process-wide singleton (re-read policy is explicit via reset())."""
    global _GUARD  # noqa: PLW0603
    with _GUARD_LOCK:
        if _GUARD is None:
            _GUARD = BudgetGuard()
        return _GUARD


def reset_guard(policy: BudgetPolicy | None = None) -> BudgetGuard:
    global _GUARD  # noqa: PLW0603
    with _GUARD_LOCK:
        _GUARD = BudgetGuard(policy)
        return _GUARD
