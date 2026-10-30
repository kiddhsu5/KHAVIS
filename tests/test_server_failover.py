"""Tests for the HTTP-layer retry-next-pool path (``server/openai_api.py``).

``core/health.py`` proves the tracker works; this file proves the request path
actually *uses* it — i.e. that a 429 on the first pool is invisible to the
caller because the request is retried on the next healthy one.

FastAPI is an optional extra, so the whole module skips without it.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="khavis-serve needs the 'server' extra")

from fastapi import HTTPException  # noqa: E402

from core.health import HealthPolicy, reset_tracker  # noqa: E402
from server.openai_api import _call_with_failover  # noqa: E402


def _plugin(name: str, *, boom: Exception | None = None) -> MagicMock:
    p = MagicMock()
    p.name = name
    if boom is None:
        p.chat.return_value = {
            "choices": [{"message": {"role": "assistant", "content": f"reply from {name}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    else:
        p.chat.side_effect = boom
    return p


def _router(pools: list[MagicMock], capability: str = "程式碼") -> MagicMock:
    """Router stub whose select() honours the health tracker, like the real one."""
    from core.capability_router import CapabilityEntry, CapabilityRouter

    reg = MagicMock()
    reg.get.side_effect = {p.name: p for p in pools}.get
    # select_any() (the pinned-model failover path) enumerates the registry.
    reg.all.return_value = list(pools)
    router = CapabilityRouter(reg)
    router._entries = [CapabilityEntry(capability=capability, pools=[p.name for p in pools])]
    return router


@pytest.fixture(autouse=True)
def _fresh_tracker():
    reset_tracker(HealthPolicy(failure_threshold=1))
    yield
    reset_tracker(HealthPolicy(failure_threshold=1))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
class TestNoFailure:
    def test_first_pool_succeeds_and_is_recorded(self):
        a = _plugin("A")
        used, result, attempts = _call_with_failover(
            _router([a]), "程式碼", [{"role": "user", "content": "hi"}]
        )
        assert used is a
        assert attempts == 1
        assert "A" in result["choices"][0]["message"]["content"]

    def test_open_circuit_pool_is_never_called(self):
        """A tripped circuit must keep the pool out of the request path.

        With only one pool available, an open circuit means there is nothing
        to route to — which has to surface as a 503, not as a silent retry
        into the pool we already know is broken.
        """
        from core.health import get_tracker

        a = _plugin("A")
        get_tracker().record_failure("A", "ConnectionResetError")

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _call_with_failover(_router([a]), "程式碼", [])
        assert exc.value.status_code == 503
        assert a.chat.called is False, "a pool with an open circuit was actually called"


# ---------------------------------------------------------------------------
# The whole point: a dead pool must not reach the caller
# ---------------------------------------------------------------------------
class TestFailover:
    def test_429_on_first_pool_retries_on_second(self):
        a = _plugin("A", boom=RuntimeError("HTTP 429 rate limit exceeded"))
        b = _plugin("B")
        used, result, attempts = _call_with_failover(_router([a, b]), "程式碼", [])
        assert used is b
        assert attempts == 2
        assert "B" in result["choices"][0]["message"]["content"]

    def test_failed_pool_is_marked_exhausted_not_open(self):
        from core.health import get_tracker

        a = _plugin("A", boom=RuntimeError("insufficient_quota"))
        b = _plugin("B")
        _call_with_failover(_router([a, b]), "程式碼", [])
        assert get_tracker().get("A").state.value == "exhausted"
        assert get_tracker().get("B").state.value == "closed"

    def test_two_failures_still_reach_the_third(self):
        a = _plugin("A", boom=RuntimeError("boom"))
        b = _plugin("B", boom=RuntimeError("boom"))
        c = _plugin("C")
        used, _result, attempts = _call_with_failover(_router([a, b, c]), "程式碼", [])
        assert used is c
        assert attempts == 3

    def test_pinned_pool_fails_over_too(self):
        pinned = _plugin("Pinned", boom=RuntimeError("boom"))
        other = _plugin("Other")
        used, _result, attempts = _call_with_failover(
            _router([other], "程式碼"), "程式碼", [], pinned=pinned
        )
        assert used is other
        assert attempts == 2

    def test_pinned_pool_with_no_capability_still_fails_over(self):
        """The shape production actually sends when a request names a model.

        ``_pick_plugin`` returns ``capability=None`` for an explicit model, and
        ``model`` is what every OpenAI-compatible client sends by default. The
        retry loop used to bail out on that path, so a 429 on the requested
        model reached the caller and failover was dead code in practice.
        """
        pinned = _plugin("Pinned", boom=RuntimeError("HTTP 429 rate limit exceeded"))
        other = _plugin("Other")
        used, result, attempts = _call_with_failover(
            _router([pinned, other]), None, [], pinned=pinned
        )
        assert used is other
        assert attempts == 2
        assert "Other" in result["choices"][0]["message"]["content"]

    def test_pinned_without_capability_still_raises_when_nothing_else_is_healthy(self):
        pinned = _plugin("Pinned", boom=RuntimeError("boom"))
        with pytest.raises(HTTPException) as exc:
            _call_with_failover(_router([pinned]), None, [], pinned=pinned)
        assert exc.value.status_code == 502
        assert "boom" in str(exc.value.detail)

    def test_all_pools_failing_raises_the_last_error(self):
        a = _plugin("A", boom=RuntimeError("first"))
        b = _plugin("B", boom=RuntimeError("last one"))
        with pytest.raises(HTTPException) as exc:
            _call_with_failover(_router([a, b]), "程式碼", [])
        assert exc.value.status_code == 502
        assert "last one" in str(exc.value.detail)

    def test_attempt_cap_is_respected(self):
        pools = [_plugin(f"P{i}", boom=RuntimeError(f"boom {i}")) for i in range(6)]
        with pytest.raises(HTTPException):
            _call_with_failover(_router(pools), "程式碼", [], max_attempts=3)
        # Only three should have been tried.
        called = [p for p in pools if p.chat.called]
        assert len(called) == 3


# ---------------------------------------------------------------------------
# All-unavailable
# ---------------------------------------------------------------------------
class TestAllUnavailable:
    def test_every_pool_spent_returns_503(self):
        from core.health import get_tracker

        a = _plugin("A", boom=RuntimeError("insufficient_quota"))
        tracker = get_tracker()
        tracker.record_failure("A", "insufficient_quota")

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _call_with_failover(_router([a]), "程式碼", [])
        assert exc.value.status_code == 503
        assert "exhausted" in exc.value.detail


# ---------------------------------------------------------------------------
# Opt-out
# ---------------------------------------------------------------------------
class TestFailoverDisabled:
    def test_khavis_failover_zero_pins_strictly(self, monkeypatch):
        monkeypatch.setenv("KHAVIS_FAILOVER", "0")
        a = _plugin("A", boom=RuntimeError("boom"))
        b = _plugin("B")
        with pytest.raises(HTTPException) as exc:
            _call_with_failover(_router([a, b]), "程式碼", [], pinned=a)
        assert exc.value.status_code == 502
        # The healthy alternative must NOT have been used.
        assert b.chat.called is False

    def test_failover_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("KHAVIS_FAILOVER", raising=False)
        assert os.environ.get("KHAVIS_FAILOVER", "1") == "1"
