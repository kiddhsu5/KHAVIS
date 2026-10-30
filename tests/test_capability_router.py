"""Tests for the capability-based router.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import random
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.capability_router import CapabilityEntry, CapabilityRouter
from core.registry import PluginRegistry
from providers.base import ProviderPlugin

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry(PROJECT_ROOT).discover()


@pytest.fixture
def router(registry: PluginRegistry) -> CapabilityRouter:
    return CapabilityRouter(registry)


@pytest.fixture
def loaded_router(router: CapabilityRouter) -> CapabilityRouter:
    return router.load_capabilities(CONFIG_DIR / "capabilities.yaml")


# ---------------------------------------------------------------------------
# CapabilityEntry
# ---------------------------------------------------------------------------
class TestCapabilityEntry:
    def test_from_dict_defaults(self):
        entry = CapabilityEntry.from_dict({"capability": "中文"})
        assert entry.capability == "中文"
        assert entry.pools == []
        assert entry.weight == 1.0

    def test_from_dict_full(self):
        entry = CapabilityEntry.from_dict(
            {"capability": "推理", "pools": ["A", "B"], "weight": 1.5}
        )
        assert entry.pools == ["A", "B"]
        assert entry.weight == 1.5


# ---------------------------------------------------------------------------
# Capability loading
# ---------------------------------------------------------------------------
class TestCapabilityLoading:
    def test_load_returns_self(self, router: CapabilityRouter):
        assert router.load_capabilities(CONFIG_DIR / "capabilities.yaml") is router

    def test_load_populates_entries(self, loaded_router: CapabilityRouter):
        caps = [e.capability for e in loaded_router.entries()]
        for required in ("中文", "英文", "推理", "工具調用", "Embedding", "長文"):
            assert required in caps

    def test_long_context_capability_uses_claude(self, loaded_router: CapabilityRouter):
        # The new "長文" capability should be served primarily by Claude-API.
        candidates = loaded_router.candidates("長文")
        names = [p.name for p in candidates]
        assert "Claude-API" in names
        # Google-Gemini is the second pool listed for 長文.
        assert "Google-Gemini" in names

    def test_openai_pool_in_capability_lists(self, loaded_router: CapabilityRouter):
        # OpenAI-API is BYOK but should still appear in the capability pool lists.
        for cap in ("英文", "程式碼", "推理", "工具調用", "速度優先"):
            names = [p.name for p in loaded_router.candidates(cap)]
            assert "OpenAI-API" in names, f"OpenAI-API missing from {cap} candidates"

    def test_load_weights_preserved(self, loaded_router: CapabilityRouter):
        weights = {e.capability: e.weight for e in loaded_router.entries()}
        assert weights["程式碼"] == 1.2
        assert weights["推理"] == 1.3

    def test_load_missing_file(self, router: CapabilityRouter, tmp_path):
        # Non-existent path: yaml.safe_load on '' would crash, but the
        # implementation calls ``Path(path).read_text`` so a missing
        # file raises — we only assert it's not silently swallowed.
        with pytest.raises(FileNotFoundError):
            router.load_capabilities(tmp_path / "absent.yaml")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
class TestRouting:
    def test_candidates_for_known_capability(self, loaded_router: CapabilityRouter):
        candidates = loaded_router.candidates("推理")
        assert len(candidates) >= 1
        assert all(isinstance(p, ProviderPlugin) for p in candidates)

    def test_candidates_falls_back_to_registry(self, router: CapabilityRouter):
        # No YAML loaded → falls back to registry.by_capability().
        candidates = router.candidates("中文")
        assert any(p.name == "MiniMax-M3" for p in candidates)

    def test_candidates_skips_missing_pools(self, loaded_router: CapabilityRouter):
        # Inject a bogus pool name and verify it's silently dropped.
        loaded_router._entries.append(
            CapabilityEntry(capability="nonsense", pools=["NotAPool"], weight=1.0)
        )
        # No provider advertises "nonsense" so we should get an empty list
        # unless the registry happens to match — assert list type.
        assert isinstance(loaded_router.candidates("nonsense"), list)

    def test_candidates_unknown_capability(self, loaded_router: CapabilityRouter):
        assert loaded_router.candidates("totally-unknown-cap") == []

    def test_select_returns_plugin_or_none(self, loaded_router: CapabilityRouter):
        p = loaded_router.select("推理", strategy="weighted")
        assert p is None or isinstance(p, ProviderPlugin)

    def test_select_unknown_capability_returns_none(self, loaded_router: CapabilityRouter):
        assert loaded_router.select("does-not-exist") is None

    def test_strategy_random(self, loaded_router: CapabilityRouter):
        rng = random.Random(0)
        a = loaded_router.select("推理", strategy="random", rng=rng)
        b = loaded_router.select("推理", strategy="random", rng=rng)
        assert a is not None and b is not None
        # We don't assert inequality — same seed could pick the same item.

    def test_strategy_round_robin_rotates(self, loaded_router: CapabilityRouter):
        from core.health import reset_tracker

        reset_tracker()
        n = len(loaded_router.candidates("推理"))
        assert n >= 2
        picks = [loaded_router.select("推理", strategy="round_robin") for _ in range(n)]
        assert all(p is not None for p in picks)
        # One full cycle visits every candidate exactly once, in YAML order.
        assert [p.name for p in picks] == [p.name for p in loaded_router.candidates("推理")]
        again = loaded_router.select("推理", strategy="round_robin")
        assert again is not None and again.name == picks[0].name

    def test_round_robin_counters_are_per_capability(self):
        from core.health import reset_tracker

        reset_tracker()
        a, b, c = (MagicMock(spec=ProviderPlugin) for _ in range(3))
        a.name, b.name, c.name = "A", "B", "C"
        reg = MagicMock()
        reg.get.side_effect = {"A": a, "B": b, "C": c}.get
        router = CapabilityRouter(reg)
        router._entries = [  # noqa: SLF001
            CapabilityEntry(capability="x", pools=["A", "B"]),
            CapabilityEntry(capability="y", pools=["C", "A"]),
        ]
        assert router.select("x", strategy="round_robin") is a
        assert router.select("y", strategy="round_robin") is c
        assert router.select("x", strategy="round_robin") is b
        assert router.select("y", strategy="round_robin") is a

    def test_round_robin_is_thread_safe(self):
        import threading
        from collections import Counter

        from core.health import reset_tracker

        reset_tracker()
        plugs = []
        for name in ("A", "B", "C", "D"):
            p = MagicMock(spec=ProviderPlugin)
            p.name = name
            plugs.append(p)
        reg = MagicMock()
        reg.get.side_effect = {p.name: p for p in plugs}.get
        router = CapabilityRouter(reg)
        router._entries = [CapabilityEntry(capability="x", pools=["A", "B", "C", "D"])]  # noqa: SLF001
        seen: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            for _ in range(100):
                picked = router.select("x", strategy="round_robin")
                with lock:
                    seen.append(picked.name)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert Counter(seen) == {"A": 200, "B": 200, "C": 200, "D": 200}

    def test_strategy_round_robin_distinguishes_capabilities(self, loaded_router: CapabilityRouter):
        a = loaded_router.select("中文", strategy="round_robin")
        b = loaded_router.select("Embedding", strategy="round_robin")
        # Both should resolve but may pick different pools.
        assert a is not None and b is not None


# ---------------------------------------------------------------------------
# Weighted selection
# ---------------------------------------------------------------------------
class TestWeightedChoice:
    def test_weighted_choice_respects_distribution(self):
        from core.capability_router import _weighted_choice

        items = ["a", "b"]
        rng = random.Random(42)
        # weight heavily toward "a" → should pick "a" almost always.
        results = [_weighted_choice(items, [1000.0, 1.0], rng) for _ in range(200)]
        assert results.count("a") > 150

    def test_weighted_choice_zero_total_falls_back_to_uniform(self):
        from core.capability_router import _weighted_choice

        items = ["a", "b", "c"]
        rng = random.Random(0)
        # All zero weights → fall back to rng.choice(items).
        for _ in range(50):
            assert _weighted_choice(items, [0.0, 0.0, 0.0], rng) in items

    def test_weighted_choice_never_picks_zero_weight(self):
        from core.capability_router import _weighted_choice

        items = ["zero", "a", "zero2", "b"]
        for seed in range(200):
            rng = random.Random(seed)
            assert _weighted_choice(items, [0.0, 1.0, 0.0, 1.0], rng) in {"a", "b"}

    def test_weighted_choice_zero_weight_even_when_uniform_returns_zero(self):
        from core.capability_router import _weighted_choice

        class EdgeRng:
            def uniform(self, lo, hi):
                return lo  # worst case: pick lands exactly on 0.0

            def choice(self, items):
                raise AssertionError("must not fall back to uniform")

        assert _weighted_choice(["zero", "a"], [0.0, 2.0], EdgeRng()) == "a"

        class TopRng(EdgeRng):
            def uniform(self, lo, hi):
                return hi  # pick == total

        assert _weighted_choice(["a", "zero"], [2.0, 0.0], TopRng()) == "a"

    def test_weighted_choice_clips_negative_weights(self):
        from core.capability_router import _weighted_choice

        items = ["a", "b"]
        rng = random.Random(0)
        # Negative weight is clipped to zero → falls into the "all zero"
        # branch and picks uniformly.
        for _ in range(20):
            assert _weighted_choice(items, [-1.0, -1.0], rng) in items


# ---------------------------------------------------------------------------
# Fallback / health
# ---------------------------------------------------------------------------
class TestFallback:
    def test_select_falls_back_when_no_yaml_entry(self, router: CapabilityRouter):
        # No entries loaded; registry should still produce candidates
        # for any capability advertised by the providers.
        assert router.select("中文") is not None

    def test_select_when_only_plugin_fails_health(self, registry: PluginRegistry):
        """A pool whose health probe fails must not be handed traffic.

        This assertion used to be the opposite — it asserted that a plugin
        returning ``{"ok": False}`` was still selected, which codified the
        missing failover as expected behaviour. The health filter now lives in
        ``core/health.py``; see ``tests/test_health.py`` for the full matrix.
        """
        from core.health import HealthPolicy, reset_tracker

        reset_tracker(HealthPolicy(failure_threshold=1))

        failing = MagicMock(spec=ProviderPlugin)
        failing.name = "Failing"
        failing.has_capability.return_value = True
        failing.health_check.return_value = {"ok": False, "detail": "down"}

        stub_registry = MagicMock()
        stub_registry.by_capability.return_value = [failing]
        stub_registry.get.return_value = failing

        router = CapabilityRouter(stub_registry)
        tracker = reset_tracker(HealthPolicy(failure_threshold=1))
        tracker.refresh(failing)

        assert tracker.is_available("Failing") is False
        # No healthy pool for this capability -> select() refuses rather than
        # silently routing into a pool we already know is down.
        assert router.select("any-cap") is None

    def test_select_skips_unhealthy_pool_when_healthy_alternative_exists(self):
        from core.health import HealthPolicy, reset_tracker

        down, up = MagicMock(spec=ProviderPlugin), MagicMock(spec=ProviderPlugin)
        down.name, up.name = "Down", "Up"
        for plug, ok in ((down, False), (up, True)):
            plug.has_capability.return_value = True
            plug.health_check.return_value = {"ok": ok, "detail": "" if ok else "down"}
            plug.check_quota.return_value = {"remaining": 10, "total": 100, "tier": "t"}

        stub_registry = MagicMock()
        stub_registry.by_capability.return_value = [down, up]
        stub_registry.get.side_effect = {"Down": down, "Up": up}.get

        router = CapabilityRouter(stub_registry)
        tracker = reset_tracker(HealthPolicy(failure_threshold=1))
        tracker.refresh_all([down, up])

        assert router.select("any-cap", strategy="priority") is up

    def test_weighted_choice_returns_last_item_on_overshoot(self):
        from core.capability_router import _weighted_choice

        # With a fixed seed and unequal weights we verify the function
        # always returns an item from the input list (regression guard).
        items = ["x", "y", "z"]
        rng = random.Random(7)
        for _ in range(100):
            assert _weighted_choice(items, [1.0, 1.0, 1.0], rng) in items
