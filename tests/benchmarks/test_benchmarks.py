"""Hot-path benchmarks for khavis.

Each test class measures a different layer of the request lifecycle.
All measurements mock external I/O so the suite runs offline.

Run::

    pytest tests/benchmarks/ -v -s
    # or:
    python scripts/run_benchmarks.py

Why plain ``time.perf_counter`` instead of ``pytest-benchmark``:
the project does not depend on ``pytest-benchmark`` (see pyproject.toml
``[project.optional-dependencies.dev]``). Adding it would be welcome
but would require a dependency change. Plain timing keeps these tests
portable.

Shared timing / mock helpers live in ``harness.py`` so this file and
``scripts/run_benchmarks.py`` cannot drift apart.

The numbers we care about:

* ``discovery_ms``        — cold-start cost of auto-loading plugins
* ``capability_load_ms``  — YAML parse + entry construction
* ``selection_ms``        — hot path; called for every request
* ``chat_roundtrip_ms``   — end-to-end with a synthetic mock registry
* ``hot_reload_apply_ms`` — capability-apply step of a YAML reload

Budgets in ``harness.BUDGETS`` are hard ceilings: a test fails when
p95 reaches the budget.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (
    BUDGETS,
    CONFIG_DIR,
    ITERS,
    PROJECT_ROOT,
    WARMUP,
    report,
    synthetic_registry,
    time_n,
)

from core.capability_router import CapabilityRouter
from core.registry import PluginRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def registry() -> PluginRegistry:
    return PluginRegistry(PROJECT_ROOT).discover()


@pytest.fixture(scope="module")
def loaded_router(registry: PluginRegistry) -> CapabilityRouter:
    return CapabilityRouter(registry).load_capabilities(CONFIG_DIR / "capabilities.yaml")


@pytest.fixture
def synthetic():
    return synthetic_registry()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
class TestDiscoveryBenchmark:
    """Cold start: how long does it take to load all plugins?

    Includes importlib, class instantiation, and YAML scan.
    """

    def test_discovery_under_budget(self):
        durations: list[float] = []
        # Warmup: skip the first call (imports, etc.)
        PluginRegistry(PROJECT_ROOT).discover()
        for _ in range(20):
            d = time_n(lambda: PluginRegistry(PROJECT_ROOT).discover(), 1)
            durations.append(d[0])

        summary = report("discovery_ms", durations)
        assert summary["p95_ms"] < BUDGETS["discovery_ms"], (
            f"discovery p95 {summary['p95_ms']:.1f}ms exceeded budget "
            f"{BUDGETS['discovery_ms']:.1f}ms"
        )


# ---------------------------------------------------------------------------
# Capability loading
# ---------------------------------------------------------------------------
class TestCapabilityLoadBenchmark:
    """YAML parse + entry construction (one-shot per reload)."""

    def test_capability_load_under_budget(self, registry: PluginRegistry):
        router = CapabilityRouter(registry)
        for _ in range(WARMUP):
            router.load_capabilities(CONFIG_DIR / "capabilities.yaml")
        durations = time_n(
            lambda: router.load_capabilities(CONFIG_DIR / "capabilities.yaml"), ITERS
        )
        summary = report("capability_load_ms", durations)
        assert summary["p95_ms"] < BUDGETS["capability_load_ms"], (
            f"capability load p95 {summary['p95_ms']:.1f}ms exceeded budget "
            f"{BUDGETS['capability_load_ms']:.1f}ms"
        )


# ---------------------------------------------------------------------------
# Selection (the hot path)
# ---------------------------------------------------------------------------
class TestSelectionBenchmark:
    """``CapabilityRouter.select()`` is called for every incoming request.

    Budget (``harness.BUDGETS['selection_ms']``) is the hard ceiling;
    observed p95 is typically well under 1 ms.
    """

    @pytest.mark.parametrize(
        "capability",
        ["中文", "英文", "程式碼", "推理", "工具調用", "速度優先", "Embedding", "長文"],
    )
    def test_select_each_capability(self, loaded_router: CapabilityRouter, capability: str):
        for _ in range(WARMUP):
            loaded_router.select(capability)
        durations = time_n(lambda: loaded_router.select(capability), ITERS)
        summary = report(f"selection_ms[{capability}]", durations)
        assert summary["p95_ms"] < BUDGETS["selection_ms"], (
            f"selection p95 for {capability!r} was "
            f"{summary['p95_ms']:.2f}ms (budget {BUDGETS['selection_ms']:.2f}ms)"
        )

    def test_select_synthetic_low_overhead(self, synthetic):
        """Confirm the router is cheap even with 12 mock candidates per cap."""
        reg, _ = synthetic
        router = CapabilityRouter(reg).load_capabilities(CONFIG_DIR / "capabilities.yaml")
        for _ in range(WARMUP):
            router.select("推理")
        durations = time_n(lambda: router.select("推理"), ITERS)
        summary = report("selection_ms[synthetic/推理]", durations)
        assert summary["p95_ms"] < BUDGETS["selection_ms"], (
            f"synthetic selection p95 {summary['p95_ms']:.2f}ms exceeded budget "
            f"{BUDGETS['selection_ms']:.2f}ms"
        )


# ---------------------------------------------------------------------------
# End-to-end roundtrip with mocked I/O
# ---------------------------------------------------------------------------
class TestChatRoundtripBenchmark:
    """End-to-end: select + invoke mocked chat() on the chosen plugin.

    Uses a synthetic mock registry so no network I/O is performed.
    Real network latency dominates on top of this; the number we care
    about is the router overhead (select + method dispatch + return).
    """

    def test_roundtrip_under_budget(self, synthetic):
        reg, plugins = synthetic
        router = CapabilityRouter(reg).load_capabilities(CONFIG_DIR / "capabilities.yaml")

        for _ in range(WARMUP):
            plugin = router.select("推理")
            assert plugin is not None
            plugin.chat([{"role": "user", "content": "ping"}])

        def _round():
            plugin = router.select("推理")
            assert plugin is not None
            plugin.chat([{"role": "user", "content": "ping"}])

        durations = time_n(_round, ITERS)
        summary = report("chat_roundtrip_ms", durations)
        assert summary["p95_ms"] < BUDGETS["chat_roundtrip_ms"], (
            f"roundtrip p95 {summary['p95_ms']:.1f}ms exceeded budget "
            f"{BUDGETS['chat_roundtrip_ms']:.1f}ms"
        )

    def test_roundtrip_three_capability_mix(self, synthetic):
        """One chat() per iteration across three capabilities (not failover).

        Measures the cost of three independent select+chat calls in a
        single loop body — a proxy for multi-turn agent traffic — not a
        pool-failure retry path.
        """
        reg, plugins = synthetic
        router = CapabilityRouter(reg).load_capabilities(CONFIG_DIR / "capabilities.yaml")

        for _ in range(WARMUP):
            for cap in ["推理", "程式碼", "英文"]:
                plugin = router.select(cap)
                assert plugin is not None
                plugin.chat([{"role": "user", "content": "ping"}])

        def _round():
            for cap in ["推理", "程式碼", "英文"]:
                plugin = router.select(cap)
                assert plugin is not None
                plugin.chat([{"role": "user", "content": "ping"}])

        durations = time_n(_round, ITERS)
        summary = report("chat_roundtrip_ms[3-cap-mix]", durations)
        # 3 caps × the per-call budget.
        assert summary["p95_ms"] < BUDGETS["chat_roundtrip_ms"] * 3, (
            f"3-cap mix p95 {summary['p95_ms']:.1f}ms exceeded budget "
            f"{BUDGETS['chat_roundtrip_ms'] * 3:.1f}ms"
        )


# ---------------------------------------------------------------------------
# Hot reload apply (YAML → entry rebuild)
# ---------------------------------------------------------------------------
class TestHotReloadApplyBenchmark:
    """Time the capability-apply step a hot-reload callback runs.

    This is ``CapabilityRouter.load_capabilities`` on a minimal YAML —
    the work done after the file watcher fires. It does not exercise the
    watcher itself (see ``tests/test_hot_reload.py``).
    """

    def test_reload_apply_under_budget(self, registry: PluginRegistry, tmp_path):
        router = CapabilityRouter(registry)
        yaml_path = tmp_path / "capabilities.yaml"
        yaml_path.write_text(
            "capabilities:\n  - capability: bench\n    pools: [MiniMax-M3]\n    weight: 1.0\n",
            encoding="utf-8",
        )

        for _ in range(WARMUP):
            router.load_capabilities(yaml_path)

        durations = time_n(lambda: router.load_capabilities(yaml_path), ITERS)
        summary = report("hot_reload_apply_ms", durations)
        assert summary["p95_ms"] < BUDGETS["hot_reload_apply_ms"], (
            f"hot reload apply p95 {summary['p95_ms']:.1f}ms exceeded budget "
            f"{BUDGETS['hot_reload_apply_ms']:.1f}ms"
        )
