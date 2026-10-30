#!/usr/bin/env python3
"""Run khavis benchmarks and print a one-shot summary.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Run from the project root::

    python scripts/run_benchmarks.py

Exits 0 if every benchmark passes its budget; 1 otherwise.

Timing / mock helpers are shared with ``tests/benchmarks/test_benchmarks.py``
via ``tests/benchmarks/harness.py`` so the two entry points cannot drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_HARNESS_DIR = PROJECT_ROOT / "tests" / "benchmarks"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

from harness import (  # noqa: E402
    BUDGETS,
    CONFIG_DIR,
    ITERS,
    WARMUP,
    report,
    synthetic_registry,
    time_n,
)

from core.capability_router import CapabilityRouter  # noqa: E402
from core.registry import PluginRegistry  # noqa: E402


def main() -> int:
    print(f"khavis benchmark suite \u2014 {ITERS} iterations each\n")

    print("--- cold start ---")
    PluginRegistry(PROJECT_ROOT).discover()  # warmup
    durations: list[float] = []
    for _ in range(20):
        durations.extend(time_n(lambda: PluginRegistry(PROJECT_ROOT).discover(), 1))
    d = report("discovery_ms", durations)
    registry = PluginRegistry(PROJECT_ROOT).discover()

    print("\n--- config load ---")
    router = CapabilityRouter(registry)
    for _ in range(WARMUP):
        router.load_capabilities(CONFIG_DIR / "capabilities.yaml")
    cap_durations = time_n(
        lambda: router.load_capabilities(CONFIG_DIR / "capabilities.yaml"), ITERS
    )
    c = report("capability_load_ms", cap_durations)

    # Selection on the real registry
    print("\n--- selection (per capability, real registry) ---")
    caps = ["中文", "英文", "程式碼", "推理", "工具調用", "速度優先", "Embedding", "長文"]
    sel_durations: list[float] = []
    for cap in caps:
        for _ in range(WARMUP):
            router.select(cap)
        durations = time_n(lambda c=cap: router.select(c), ITERS)
        d_sel = report(f"selection_ms[{cap}]", durations)
        sel_durations.append(d_sel["p95_ms"])

    # Roundtrip with synthetic registry (mocked I/O)
    print("\n--- end-to-end (mocked I/O) ---")
    reg, _ = synthetic_registry()
    synth_router = CapabilityRouter(reg).load_capabilities(CONFIG_DIR / "capabilities.yaml")

    def _round():
        plugin = synth_router.select("推理")
        assert plugin is not None
        plugin.chat([{"role": "user", "content": "ping"}])

    for _ in range(WARMUP):
        _round()
    r = report("chat_roundtrip_ms", time_n(_round, ITERS))

    # Final pass/fail — budgets are hard ceilings (p95 must stay under).
    print("\n--- budgets ---")
    failed = False
    checks = [
        ("discovery_ms", d["p95_ms"], BUDGETS["discovery_ms"]),
        ("capability_load_ms", c["p95_ms"], BUDGETS["capability_load_ms"]),
        ("chat_roundtrip_ms", r["p95_ms"], BUDGETS["chat_roundtrip_ms"]),
    ]
    for name, actual, budget in checks:
        ok = actual < budget
        if not ok:
            failed = True
        flag = "OK  " if ok else "FAIL"
        print(f"  [{flag}] {name:>22}  p95={actual:7.3f} ms  budget={budget:7.1f} ms")

    # Each capability is already checked 1x above; the aggregate max must
    # also stay under the same ceiling (it is the max of those p95s).
    sel_max = max(sel_durations) if sel_durations else 0.0
    sel_budget = BUDGETS["selection_ms"]
    ok = sel_max < sel_budget
    if not ok:
        failed = True
    flag = "OK  " if ok else "FAIL"
    print(
        f"  [{flag}] {'selection_ms[max cap]':>22}  p95={sel_max:7.3f} ms  "
        f"budget={sel_budget:7.1f} ms"
    )

    print()
    if failed:
        print("one or more benchmarks exceeded budget")
        return 1
    print("all benchmarks within budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
