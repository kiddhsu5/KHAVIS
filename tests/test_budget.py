"""Tests for core.budget (v0.2.0 token-aware cost budget)."""

from __future__ import annotations

from pathlib import Path

from core.budget import (
    BudgetGuard,
    BudgetPolicy,
    estimate_tokens,
    get_guard,
    load_policy,
    reset_guard,
)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("x" * 400) == 100


def test_load_policy_defaults(tmp_path: Path):
    p = tmp_path / "budgets.yaml"
    p.write_text(
        """
policy:
  max_tokens_per_request: 100
  max_tokens_per_session: 1000
  max_cost_per_session_usd: 0
  window_s: 60
pricing_usd_per_1k:
  X: 1.0
""",
        encoding="utf-8",
    )
    pol = load_policy(p)
    assert pol.max_tokens_per_request == 100
    assert pol.pricing_usd_per_1k["X"] == 1.0


def test_env_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KHAVIS_BUDGET_MAX_TOKENS_REQUEST", "777")
    pol = load_policy(tmp_path / "missing.yaml")
    assert pol.max_tokens_per_request == 777


def test_request_cap_blocks():
    guard = BudgetGuard(BudgetPolicy(max_tokens_per_request=10, max_tokens_per_session=10**9))
    assert guard.allow(session_id="s", pool="P", prompt_chars=8) is True
    assert guard.allow(session_id="s", pool="P", prompt_chars=8000) is False
    assert "max_tokens_per_request" in guard.reason()


def test_session_token_cap():
    guard = BudgetGuard(
        BudgetPolicy(max_tokens_per_request=10_000, max_tokens_per_session=100, window_s=3600)
    )
    guard.record(session_id="s", pool="P", usage={"prompt_tokens": 90, "completion_tokens": 0})
    assert guard.allow(session_id="s", pool="P", prompt_chars=80) is False
    assert "max_tokens_per_session" in guard.reason()
    # other session unaffected
    assert guard.allow(session_id="other", pool="P", prompt_chars=4) is True


def test_session_cost_cap():
    guard = BudgetGuard(
        BudgetPolicy(
            max_tokens_per_request=10_000,
            max_tokens_per_session=10**9,
            max_cost_per_session_usd=0.01,
            pricing_usd_per_1k={"X": 10.0},  # $10 / 1k tokens
        )
    )
    # 20 tokens ≈ $0.2 — already over
    guard.record(session_id="s", pool="X", usage={"prompt_tokens": 20, "completion_tokens": 0})
    assert guard.allow(session_id="s", pool="X", prompt_chars=4) is False
    assert "max_cost_per_session_usd" in guard.reason()


def test_window_prunes_old_events():
    guard = BudgetGuard(BudgetPolicy(max_tokens_per_session=50, window_s=0.01))
    guard.record(session_id="s", pool="P", usage={"prompt_tokens": 40, "completion_tokens": 0})
    import time

    time.sleep(0.02)
    assert guard.allow(session_id="s", pool="P", prompt_chars=4) is True


def test_status_shape():
    guard = BudgetGuard()
    guard.record(
        session_id="s", pool="MiMo-Code", usage={"prompt_tokens": 5, "completion_tokens": 5}
    )
    st = guard.status()
    assert "policy" in st and "sessions" in st and "by_pool" in st
    assert st["sessions"]["s"]["tokens"] == 10
    assert "MiMo-Code" in st["by_pool"]


def test_singleton_reset():
    g1 = get_guard()
    g2 = reset_guard(BudgetPolicy(max_tokens_per_request=1))
    assert g2 is get_guard()
    assert g2.policy.max_tokens_per_request == 1
    assert g2 is not g1


# ---------------------------------------------------------------------------
# Review 2026-10-02: per-call reasons, atomic reserve/settle, config_dir()
# ---------------------------------------------------------------------------
def test_check_returns_reason_without_shared_state():
    guard = BudgetGuard(BudgetPolicy(max_tokens_per_request=10, max_tokens_per_session=10**9))
    ok, reason = guard.check(session_id="s", pool="P", prompt_chars=8000)
    assert ok is False and "max_tokens_per_request" in reason
    ok2, reason2 = guard.check(session_id="s", pool="P", prompt_chars=8)
    assert ok2 is True and reason2 == ""
    # check() never touches the legacy shared reason
    assert guard.reason() == ""


def test_allow_is_still_compatible():
    guard = BudgetGuard(BudgetPolicy(max_tokens_per_request=10, max_tokens_per_session=10**9))
    assert guard.allow(session_id="s", pool="P", prompt_chars=8000) is False
    assert "max_tokens_per_request" in guard.reason()


def test_reserve_books_estimate_and_record_settles():
    guard = BudgetGuard(
        BudgetPolicy(max_tokens_per_request=10_000, max_tokens_per_session=100, window_s=3600)
    )
    ok, reason, rid = guard.reserve(session_id="s", pool="P", prompt_chars=240)  # 60 tokens
    assert ok and reason == "" and rid
    assert guard.session_usage("s")["tokens"] == 60
    # A second concurrent request of the same size no longer fits.
    ok2, reason2, rid2 = guard.reserve(session_id="s", pool="P", prompt_chars=240)
    assert ok2 is False and rid2 is None and "max_tokens_per_session" in reason2
    # Settle with the real (smaller) usage: the reservation is replaced.
    guard.record(
        session_id="s",
        pool="P",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        reservation=rid,
    )
    assert guard.session_usage("s")["tokens"] == 15


def test_release_drops_reservation():
    guard = BudgetGuard(BudgetPolicy(max_tokens_per_request=10_000, max_tokens_per_session=100))
    ok, _reason, rid = guard.reserve(session_id="s", pool="P", prompt_chars=200)
    assert ok
    assert guard.release(rid, session_id="s") is True
    assert guard.session_usage("s")["tokens"] == 0
    assert guard.release(rid, session_id="s") is False


def test_concurrent_reserves_never_exceed_cap():
    import threading

    guard = BudgetGuard(
        BudgetPolicy(max_tokens_per_request=10_000, max_tokens_per_session=1_000, window_s=3600)
    )
    granted: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        ok, _r, _rid = guard.reserve(session_id="s", pool="P", prompt_chars=400)  # 100 tokens
        with lock:
            granted.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert granted.count(True) == 10
    assert guard.session_usage("s")["tokens"] == 1_000


def test_expired_reservation_stops_counting(monkeypatch):
    import core.budget as budget_mod

    monkeypatch.setattr(budget_mod, "RESERVATION_TTL_S", -1.0)
    guard = BudgetGuard(BudgetPolicy(max_tokens_per_request=10_000, max_tokens_per_session=100))
    ok, _r, rid = guard.reserve(session_id="s", pool="P", prompt_chars=200)
    assert ok and rid
    assert guard.session_usage("s")["tokens"] == 0


def test_estimate_from_chars_does_not_allocate():
    from core.budget import estimate_tokens_from_chars

    assert estimate_tokens_from_chars(0) == 0
    assert estimate_tokens_from_chars(3) == 1
    assert estimate_tokens_from_chars(10**12) == 250_000_000_000


def test_load_policy_uses_config_dir(tmp_path: Path, monkeypatch):
    (tmp_path / "budgets.yaml").write_text(
        "policy:\n  max_tokens_per_request: 4242\n", encoding="utf-8"
    )
    monkeypatch.setenv("KHAVIS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("KHAVIS_BUDGET_MAX_TOKENS_REQUEST", raising=False)
    assert load_policy().max_tokens_per_request == 4242


def test_router_uses_caller_session_for_budget():
    from unittest.mock import MagicMock

    from core.capability_router import CapabilityEntry, CapabilityRouter
    from core.health import reset_tracker

    reset_tracker()
    guard = reset_guard(
        BudgetPolicy(
            max_tokens_per_request=10_000,
            max_tokens_per_session=100,
            max_cost_per_session_usd=0,
        )
    )
    a, b = MagicMock(), MagicMock()
    a.name, b.name = "A", "B"
    reg = MagicMock()
    reg.get.side_effect = {"A": a, "B": b}.get
    router = CapabilityRouter(reg)
    router._entries = [CapabilityEntry(capability="x", pools=["A", "B"])]  # noqa: SLF001
    guard.record(session_id="hdr-session", pool="A", usage={"total_tokens": 100})
    # Session over cap: all pools filtered -> falls back to the health set,
    # routing does not silently look at a different session.
    assert router.select("x", strategy="priority", session_id="hdr-session") is a
    assert guard.check(session_id="hdr-session", pool="A", prompt_chars=4)[0] is False
    assert guard.check(session_id="other", pool="A", prompt_chars=4)[0] is True
    reset_guard()
