"""Tests for quota-aware health tracking and circuit breaking.

These cover the behaviour the "zero quota interruption" promise depends on:
a pool that 429s stops receiving traffic, a broken pool trips a circuit, and
the router stops handing requests to either one.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from core.capability_router import CapabilityEntry, CapabilityRouter
from core.health import (
    HealthPolicy,
    PoolHealthTracker,
    PoolState,
    load_policy,
    reset_tracker,
)


def _plugin(name: str, *, ok: bool = True, remaining: int | None = None) -> MagicMock:
    p = MagicMock()
    p.name = name
    p.health_check.return_value = {"ok": ok, "detail": "" if ok else "down"}
    p.check_quota.return_value = {
        "remaining": remaining,
        "total": 1000,
        "tier": "test",
    }
    return p


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
class TestPolicy:
    def test_defaults_are_sane(self):
        pol = HealthPolicy.from_dict({})
        assert pol.failure_threshold >= 1
        assert pol.cooldown_s >= 0
        assert pol.on_all_unavailable == "fail"

    def test_env_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KHAVIS_HEALTH_FAILURE_THRESHOLD", "7")
        monkeypatch.setenv("KHAVIS_HEALTH_COOLDOWN_S", "2.5")
        pol = load_policy(tmp_path / "does-not-exist.yaml")
        assert pol.failure_threshold == 7
        assert pol.cooldown_s == 2.5

    def test_missing_file_falls_back_to_defaults(self, tmp_path):
        pol = load_policy(tmp_path / "nope.yaml")
        assert pol.failure_threshold == HealthPolicy.from_dict({}).failure_threshold


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------
class TestCircuitBreaker:
    def test_cold_start_pool_is_available(self):
        t = PoolHealthTracker()
        assert t.is_available("never-seen") is True
        assert t.get("never-seen").state is PoolState.UNKNOWN

    def test_failures_below_threshold_stay_available(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=3))
        t.record_failure("a", "boom")
        t.record_failure("a", "boom")
        assert t.is_available("a") is True

    def test_threshold_trips_circuit(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=2))
        t.record_failure("a", "boom")
        t.record_failure("a", "boom")
        assert t.get("a").state is PoolState.OPEN
        assert t.is_available("a") is False

    def test_success_resets_failure_streak(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=2))
        t.record_failure("a", "boom")
        t.record_success("a")
        t.record_failure("a", "boom")
        assert t.get("a").state is not PoolState.OPEN

    def test_open_circuit_recovers_after_cooldown(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, cooldown_s=0.05))
        t.record_failure("a", "boom")
        assert t.is_available("a") is False
        time.sleep(0.06)
        assert t.is_available("a") is True
        assert t.get("a").state is PoolState.HALF_OPEN

    def test_half_open_success_closes_circuit(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, cooldown_s=0.01))
        t.record_failure("a", "boom")
        time.sleep(0.02)
        assert t.is_available("a") is True  # promotes to half-open
        t.record_success("a")
        assert t.get("a").state is PoolState.CLOSED

    def test_half_open_failure_reopens_circuit(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, cooldown_s=0.01))
        t.record_failure("a", "boom")
        time.sleep(0.02)
        t.is_available("a")
        t.record_failure("a", "boom again")
        assert t.get("a").state is PoolState.OPEN
        assert t.is_available("a") is False

    def test_force_close_overrides(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", "boom")
        t.force_close("a")
        assert t.is_available("a") is True
        assert t.get("a").state is PoolState.CLOSED


# ---------------------------------------------------------------------------
# Quota exhaustion is NOT a broken circuit
# ---------------------------------------------------------------------------
class TestQuotaExhaustion:
    @pytest.mark.parametrize(
        "message",
        [
            "HTTP 429 Too Many Requests",
            "rate limit exceeded",
            "You exceeded your current quota",
            "insufficient_quota",
            "RESOURCE_EXHAUSTED: rate limit",
        ],
    )
    def test_quota_errors_mark_exhausted_not_open(self, message):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", message)
        assert t.get("a").state is PoolState.EXHAUSTED
        assert t.is_available("a") is False

    def test_non_quota_error_trips_circuit_instead(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", "ConnectionResetError")
        assert t.get("a").state is PoolState.OPEN

    def test_success_after_exhaustion_clears_it(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", "429")
        assert t.is_available("a") is False
        t.record_success("a")
        assert t.get("a").state is PoolState.CLOSED

    def test_refresh_marks_zero_remaining_exhausted(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=99, respect_quota=True))
        hp = t.refresh(_plugin("a", ok=True, remaining=0))
        assert hp.state is PoolState.EXHAUSTED
        assert t.is_available("a") is False

    def test_refresh_keeps_pool_closed_when_quota_left(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=99, respect_quota=True))
        hp = t.refresh(_plugin("a", ok=True, remaining=42))
        assert hp.state is PoolState.CLOSED
        assert hp.quota_remaining == 42
        assert t.is_available("a") is True

    def test_respect_quota_false_ignores_exhaustion(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=99, respect_quota=False))
        hp = t.refresh(_plugin("a", ok=True, remaining=0))
        assert hp.state is PoolState.CLOSED
        assert t.is_available("a") is True


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
class TestRefresh:
    def test_raising_health_check_opens_circuit(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        p = _plugin("a")
        p.health_check.side_effect = RuntimeError("dns gone")
        hp = t.refresh(p)
        assert hp.state is PoolState.OPEN
        assert "health_check raised" in (hp.last_error or "")

    def test_raising_check_quota_does_not_crash(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=99))
        p = _plugin("a", ok=True, remaining=10)
        p.check_quota.side_effect = RuntimeError("no such endpoint")
        hp = t.refresh(p)
        assert hp.state is PoolState.CLOSED

    def test_unhealthy_pool_below_threshold_stays_closed(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=3))
        hp = t.refresh(_plugin("a", ok=False))
        assert hp.state is not PoolState.OPEN

    def test_refresh_all_never_raises(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))

        class Exploding:
            name = "boom"

            def health_check(self):
                raise ValueError("nope")

            def check_quota(self):
                return {"remaining": 1}

        good = _plugin("good", ok=True, remaining=5)
        snap = t.refresh_all([Exploding(), good])
        assert snap["good"]["state"] == "closed"
        assert snap["boom"]["state"] == "open"

    def test_filter_available_respects_order_and_exclude(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("b", "boom")
        kept = t.filter_available(["a", "b", "c"], exclude=["a"])
        assert kept == ["c"]


# ---------------------------------------------------------------------------
# Router integration — this is the regression that used to be asserted as
# correct behaviour: a pool failing health_check used to still be selected.
# ---------------------------------------------------------------------------
class TestRouterHealthFiltering:
    def _router(self, registry, *entries) -> CapabilityRouter:
        r = CapabilityRouter(registry)
        r._entries = list(entries)  # noqa: SLF001 - deliberate: skip YAML I/O
        return r

    def test_router_skips_pool_with_open_circuit(self):
        reset_tracker(HealthPolicy(failure_threshold=1))
        from core.health import get_tracker

        a, b = _plugin("A"), _plugin("B")
        reg = MagicMock()
        reg.get.side_effect = {"A": a, "B": b}.get
        router = self._router(reg, CapabilityEntry(capability="程式碼", pools=["A", "B"]))

        get_tracker().record_failure("A", "boom")
        picked = router.select("程式碼", strategy="priority")
        assert picked is b

    def test_router_returns_none_when_all_circuits_open(self):
        reset_tracker(HealthPolicy(failure_threshold=1))
        from core.health import get_tracker

        a = _plugin("A")
        reg = MagicMock()
        reg.get.side_effect = {"A": a}.get
        router = self._router(reg, CapabilityEntry(capability="程式碼", pools=["A"]))

        get_tracker().record_failure("A", "boom")
        assert router.select("程式碼", strategy="priority") is None
        assert "circuit open" in router.explain_unavailable("程式碼")

    def test_router_exclude_skips_already_tried_pools(self):
        reset_tracker(HealthPolicy(failure_threshold=99))
        a, b = _plugin("A"), _plugin("B")
        reg = MagicMock()
        reg.get.side_effect = {"A": a, "B": b}.get
        router = self._router(reg, CapabilityEntry(capability="程式碼", pools=["A", "B"]))
        assert router.select("程式碼", strategy="priority", exclude=["A"]) is b

    def test_filter_router_false_disables_health_filtering(self):
        reset_tracker(HealthPolicy(failure_threshold=1, filter_router=False))
        from core.health import get_tracker

        a = _plugin("A")
        reg = MagicMock()
        reg.get.side_effect = {"A": a}.get
        router = self._router(reg, CapabilityEntry(capability="程式碼", pools=["A"]))
        get_tracker().record_failure("A", "boom")
        assert router.select("程式碼", strategy="priority") is a

    def test_priority_strategy_follows_yaml_order(self):
        reset_tracker(HealthPolicy(failure_threshold=99))
        a, b = _plugin("A"), _plugin("B")
        reg = MagicMock()
        reg.get.side_effect = {"A": a, "B": b}.get
        router = self._router(reg, CapabilityEntry(capability="程式碼", pools=["B", "A"]))
        assert router.select("程式碼", strategy="priority") is b


# ---------------------------------------------------------------------------
# Per-pool weights
# ---------------------------------------------------------------------------
class TestPoolWeights:
    def test_list_form_applies_entry_weight_to_every_pool(self):
        e = CapabilityEntry.from_dict({"capability": "x", "pools": ["A", "B"], "weight": 2.0})
        assert e.pools == ["A", "B"]
        assert e.weight_for("A") == 2.0
        assert e.weight_for("B") == 2.0

    def test_map_form_supports_per_pool_weights(self):
        e = CapabilityEntry.from_dict({"capability": "x", "pools": {"A": 1.0, "B": 0.85}})
        assert e.weight_for("A") == 1.0
        assert e.weight_for("B") == 0.85

    def test_unknown_pool_is_neutral(self):
        e = CapabilityEntry.from_dict({"capability": "x", "pools": ["A"]})
        assert e.weight_for("Z") == 1.0


# ---------------------------------------------------------------------------
# Review 2026-10-02: single half-open probe, strict HALF_OPEN failure,
# refresh cannot close a tripped circuit, precise quota detection.
# ---------------------------------------------------------------------------
class TestSingleProbe:
    def _tripped(self, **kw) -> PoolHealthTracker:
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, cooldown_s=10.0, **kw))
        t.record_failure("a", "boom")
        return t

    def test_only_one_probe_in_flight(self):
        t = self._tripped()
        opened = t.get("a").opened_at
        assert opened is not None
        later = opened + 11.0
        assert t.is_available("a", now=later) is True
        assert t.get("a").state is PoolState.HALF_OPEN
        # Every other caller is refused until the probe reports back.
        assert t.is_available("a", now=later + 0.1) is False
        assert t.is_available("a", now=later + 5.0) is False
        assert "probe in flight" in t.explain("a")

    def test_probe_timeout_allows_next_probe(self):
        t = self._tripped()
        opened = t.get("a").opened_at
        assert opened is not None
        assert t.is_available("a", now=opened + 11.0) is True
        assert t.is_available("a", now=opened + 15.0) is False
        # probe_timeout_s == cooldown_s: after that a new probe may go.
        assert t.is_available("a", now=opened + 21.5) is True
        assert t.is_available("a", now=opened + 21.6) is False

    def test_success_clears_probe_flag(self):
        t = PoolHealthTracker(
            HealthPolicy(failure_threshold=1, cooldown_s=10.0, success_threshold=2)
        )
        t.record_failure("a", "boom")
        opened = t.get("a").opened_at
        assert opened is not None
        assert t.is_available("a", now=opened + 11.0) is True
        t.record_success("a")
        # Still HALF_OPEN (needs 2 successes) but the next probe may go.
        assert t.get("a").state is PoolState.HALF_OPEN
        assert t.is_available("a", now=opened + 11.1) is True
        t.record_success("a")
        assert t.get("a").state is PoolState.CLOSED

    def test_concurrent_callers_get_exactly_one_probe(self):
        import threading

        t = self._tripped()
        opened = t.get("a").opened_at
        assert opened is not None
        results: list[bool] = []
        lock = threading.Lock()

        def worker() -> None:
            ok = t.is_available("a", now=opened + 11.0)
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert results.count(True) == 1

    def test_single_probe_holds_even_with_a_zero_cooldown(self):
        """``cooldown_s=0`` must not dissolve the single-probe guarantee.

        ``probe_timeout_s`` used to return ``cooldown_s`` directly, so a zero
        cooldown made the in-flight guard ``(now - probe_started_at) < 0.0`` —
        permanently false. Every concurrent caller then sailed into HALF_OPEN
        at once, which is the stampede the breaker exists to prevent. A zero
        cooldown is a legal setting (``KHAVIS_HEALTH_COOLDOWN_S=0``, and
        ``max(0.0, ...)`` on a hand-edited config).
        """
        import threading

        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, cooldown_s=0.0))
        t.record_failure("a", "boom")
        assert t.get("a").state is PoolState.OPEN

        results: list[bool] = []
        lock = threading.Lock()
        barrier = threading.Barrier(64)

        def worker() -> None:
            barrier.wait()  # maximise the chance of a real interleaving
            ok = t.is_available("a")
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(64)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert results.count(True) == 1, f"{results.count(True)} of 64 threads got the probe"

    def test_filter_available_does_not_claim_probes(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, cooldown_s=0.0))
        t.record_failure("a", "boom")
        t.record_failure("b", "boom")
        assert t.filter_available(["a", "b"]) == ["a", "b"]
        # Listing did not move anything to HALF_OPEN nor burn the probe.
        assert t.get("a").state is PoolState.OPEN
        assert t.get("b").state is PoolState.OPEN
        assert t.get("a").probe_started_at is None

    def test_router_claims_only_the_selected_pool(self):
        reset_tracker(HealthPolicy(failure_threshold=1, cooldown_s=0.0))
        from core.health import get_tracker

        a, b = _plugin("A"), _plugin("B")
        reg = MagicMock()
        reg.get.side_effect = {"A": a, "B": b}.get
        router = CapabilityRouter(reg)
        router._entries = [CapabilityEntry(capability="x", pools=["A", "B"])]  # noqa: SLF001
        tracker = get_tracker()
        tracker.record_failure("A", "boom")
        tracker.record_failure("B", "boom")
        assert router.select("x", strategy="priority") is a
        assert tracker.get("A").state is PoolState.HALF_OPEN
        assert tracker.get("B").state is PoolState.OPEN
        # A's probe is in flight -> next caller is routed to B's probe.
        tracker.policy.cooldown_s = 60.0
        tracker.get("B").opened_at = 0.0  # B's cooldown long over
        assert router.select("x", strategy="priority") is b


class TestHalfOpenFailure:
    def test_any_half_open_failure_reopens_even_below_threshold(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=3, cooldown_s=10.0))
        for _ in range(3):
            t.record_failure("a", "boom")
        first_open = t.get("a").opened_at
        assert first_open is not None
        assert t.is_available("a", now=first_open + 11.0) is True
        t.record_success("a")  # closes (success_threshold=1)
        for _ in range(3):
            t.record_failure("a", "boom")
        opened = t.get("a").opened_at
        assert opened is not None
        assert t.is_available("a", now=opened + 11.0) is True
        # Reset streak to prove HALF_OPEN failure does not need the threshold.
        t.get("a").consecutive_failures = 0
        t.record_failure("a", "still broken")
        hp = t.get("a")
        assert hp.state is PoolState.OPEN
        assert hp.opened_at is not None and hp.opened_at > opened
        assert hp.probe_started_at is None


class TestRefreshDoesNotCloseCircuits:
    def test_refresh_keeps_open_circuit_open(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=2, cooldown_s=0.0))
        t.record_failure("a", "boom")
        t.record_failure("a", "boom")
        hp = t.refresh(_plugin("a", ok=True, remaining=50))
        assert hp.state is PoolState.OPEN
        assert hp.consecutive_failures == 2
        assert hp.quota_remaining == 50  # quota snapshot still updated

    def test_refresh_keeps_half_open_and_probe(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, cooldown_s=0.0))
        t.record_failure("a", "boom")
        assert t.is_available("a") is True
        hp = t.refresh(_plugin("a", ok=True, remaining=5))
        assert hp.state is PoolState.HALF_OPEN
        assert hp.probe_started_at is not None

    def test_refresh_clears_exhaustion_only_on_positive_quota(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", "insufficient_quota")
        assert t.refresh(_plugin("a", ok=True, remaining=None)).state is PoolState.EXHAUSTED
        assert t.refresh(_plugin("a", ok=True, remaining=7)).state is PoolState.CLOSED

    def test_health_check_runs_outside_the_lock(self):
        import threading

        t = PoolHealthTracker(HealthPolicy(failure_threshold=99))
        entered = threading.Event()
        release = threading.Event()
        p = _plugin("slow", ok=True, remaining=5)

        def slow_health():
            entered.set()
            release.wait(2.0)
            return {"ok": True}

        p.health_check.side_effect = slow_health
        th = threading.Thread(target=t.refresh, args=(p,))
        th.start()
        assert entered.wait(2.0)
        # While the probe blocks, the request path must not.
        done = threading.Event()

        def request_path():
            t.record_failure("other", "boom")
            t.is_available("other")
            done.set()

        rp = threading.Thread(target=request_path)
        rp.start()
        assert done.wait(1.0), "tracker lock held during health_check()"
        release.set()
        th.join(2.0)
        rp.join(1.0)
        assert t.get("slow").state is PoolState.CLOSED


class _HTTPError(Exception):
    def __init__(self, msg: str, status_code: int | None = None, response=None):
        super().__init__(msg)
        self.status_code = status_code
        if response is not None:
            self.response = response


class TestQuotaDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "quota_project_id is not set",
            "connection to port 14290 refused",
            "request 4291 failed",
            "moderate limit reached",  # contains "rate limit" without a boundary
            "invalid quota configuration",
        ],
    )
    def test_loose_substrings_are_not_quota(self, message):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", message)
        assert t.get("a").state is PoolState.OPEN

    def test_status_code_429_attribute_is_quota(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", _HTTPError("upstream said no", status_code=429))
        assert t.get("a").state is PoolState.EXHAUSTED

    def test_response_status_code_is_used(self):
        resp = MagicMock()
        resp.status_code = 429
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", _HTTPError("nope", response=resp))
        assert t.get("a").state is PoolState.EXHAUSTED

    def test_explicit_status_kwarg(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", "whatever", status_code=429)
        assert t.get("a").state is PoolState.EXHAUSTED

    def test_non_429_status_ignores_bare_429_in_message(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", _HTTPError("trace id 429 internal error", status_code=500))
        assert t.get("a").state is PoolState.OPEN

    def test_rate_limited_phrase(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", "You are being rate-limited")
        assert t.get("a").state is PoolState.EXHAUSTED

    @pytest.mark.parametrize(
        ("status", "message"),
        [
            (400, "Invalid input: your rate limit config is malformed"),
            (403, "Your account rate-limited tier lacks access to this model"),
            (500, "upstream 500: Too Many Requests in queue"),
            (400, "insufficient_quota field is required"),
        ],
    )
    def test_structured_non_429_status_is_never_promoted_to_quota(self, status, message):
        """A broken *request* is not a spent *account*.

        EXHAUSTED is only left by a successful call or a quota refresh. Routing
        a request that can never succeed into it poisoned the pool for the
        whole exhausted_cooldown_s with no way back. The circuit breaker is the
        right destination: it is bounded and self-healing.
        """
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", _HTTPError(message, status_code=status))
        assert t.get("a").state is PoolState.OPEN
        assert t.is_available("a") is False

    def test_misclassified_pool_still_recovers(self):
        """The consequence that made the misjudgement serious, pinned down.

        A 400 that can never succeed must still leave a pool the breaker can
        bring back. Being misrouted into EXHAUSTED meant the only exits were a
        successful call (impossible for this request) and a quota refresh, so
        a single malformed request parked a healthy pool for the full
        ``exhausted_cooldown_s``. Recovery goes through the normal
        cooldown -> single probe -> success path.
        """
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, cooldown_s=10.0))
        t.record_failure("a", _HTTPError("rate limit config is malformed", status_code=400))
        assert t.get("a").state is PoolState.OPEN
        assert t.is_available("a") is False

        opened = t.get("a").opened_at
        assert opened is not None
        # Still inside the cooldown: nobody gets through.
        assert t.is_available("a", now=opened + 1.0) is False
        # After it: exactly one probe, and that probe's success restores it.
        assert t.is_available("a", now=opened + 11.0) is True
        t.record_success("a")
        assert t.get("a").state is PoolState.CLOSED
        assert t.is_available("a") is True


class TestExhaustedRecovery:
    def test_exhausted_recovers_by_probe_after_cooldown(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, exhausted_cooldown_s=30.0))
        t.record_failure("a", "429 Too Many Requests")
        hp = t.get("a")
        assert hp.state is PoolState.EXHAUSTED and hp.exhausted_at is not None
        at = hp.exhausted_at
        assert t.is_available("a", now=at + 10) is False
        assert t.is_available("a", now=at + 31) is True
        assert t.get("a").state is PoolState.HALF_OPEN
        assert t.is_available("a", now=at + 31.5) is False
        t.record_success("a")
        assert t.get("a").state is PoolState.CLOSED

    def test_exhausted_probe_quota_error_re_exhausts(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, exhausted_cooldown_s=30.0))
        t.record_failure("a", "insufficient_quota")
        at = t.get("a").exhausted_at
        assert at is not None
        assert t.is_available("a", now=at + 31) is True
        t.record_failure("a", "insufficient_quota")
        hp = t.get("a")
        assert hp.state is PoolState.EXHAUSTED
        assert hp.exhausted_at is not None and hp.exhausted_at > at

    def test_zero_cooldown_disables_time_recovery(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, exhausted_cooldown_s=0.0))
        t.record_failure("a", "insufficient_quota")
        assert t.is_available("a", now=time.monotonic() + 10_000) is False


class TestRedaction:
    def test_last_error_is_redacted(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        t.record_failure("a", "401 Incorrect API key provided: sk-proj-abcdef1234567890")
        err = t.get("a").last_error or ""
        assert "abcdef1234567890" not in err
        assert "sk-***" in err

    def test_health_check_exception_is_redacted(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1))
        p = _plugin("a")
        p.health_check.side_effect = RuntimeError("GET /v1?key=AIzaSyA1234567890abcdef failed")
        hp = t.refresh(p)
        assert "AIzaSyA1234567890abcdef" not in (hp.last_error or "")


class TestOnAllUnavailable:
    def test_fail_returns_empty(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, on_all_unavailable="fail"))
        t.record_failure("a", "boom")
        assert t.filter_available(["a"]) == []

    def test_degrade_returns_original_candidates(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, on_all_unavailable="degrade"))
        t.record_failure("a", "boom")
        t.record_failure("b", "boom")
        assert t.filter_available(["a", "b", "c"], exclude=["c"]) == ["a", "b"]

    def test_degrade_only_when_everything_is_down(self):
        t = PoolHealthTracker(HealthPolicy(failure_threshold=1, on_all_unavailable="degrade"))
        t.record_failure("a", "boom")
        assert t.filter_available(["a", "b"]) == ["b"]

    def test_router_degrades(self):
        reset_tracker(HealthPolicy(failure_threshold=1, on_all_unavailable="degrade"))
        from core.health import get_tracker

        a = _plugin("A")
        reg = MagicMock()
        reg.get.side_effect = {"A": a}.get
        router = CapabilityRouter(reg)
        router._entries = [CapabilityEntry(capability="x", pools=["A"])]  # noqa: SLF001
        get_tracker().record_failure("A", "boom")
        assert router.select("x", strategy="priority") is a
        # exclude still wins: a retry loop terminates.
        assert router.select("x", strategy="priority", exclude=["A"]) is None

    def test_invalid_value_falls_back_to_fail(self):
        assert HealthPolicy.from_dict({"on_all_unavailable": "yolo"}).on_all_unavailable == "fail"

    def test_env_overrides_for_new_fields(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KHAVIS_HEALTH_ALLOW_HALF_OPEN", "false")
        monkeypatch.setenv("KHAVIS_HEALTH_ON_ALL_UNAVAILABLE", "degrade")
        monkeypatch.setenv("KHAVIS_HEALTH_EXHAUSTED_COOLDOWN_S", "12")
        pol = load_policy(tmp_path / "none.yaml")
        assert pol.allow_half_open is False
        assert pol.on_all_unavailable == "degrade"
        assert pol.exhausted_cooldown_s == 12.0
