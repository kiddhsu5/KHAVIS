"""Shared timing / mock helpers for khavis benchmarks.

Used by ``scripts/run_benchmarks.py`` and ``tests/benchmarks/test_benchmarks.py``
so the two entry points cannot drift apart.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.registry import PluginRegistry  # noqa: E402
from providers.base import ProviderPlugin  # noqa: E402

CONFIG_DIR = PROJECT_ROOT / "config"

ITERS = 200
WARMUP = 10

# Hard ceilings: a measurement fails when p95 reaches the budget (1x).
BUDGETS = {
    "discovery_ms": 200.0,
    "capability_load_ms": 50.0,
    "selection_ms": 2.0,
    "chat_roundtrip_ms": 10.0,
    "hot_reload_apply_ms": 50.0,
}


def time_n(fn, n: int) -> list[float]:
    """Run ``fn`` ``n`` times and return per-call durations in ms."""
    durations: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        durations.append((time.perf_counter() - t0) * 1000.0)
    return durations


def report(name: str, durations: list[float]) -> dict[str, float]:
    """Print and return min/p50/p95/max/mean (ms)."""
    s = sorted(durations)
    summary = {
        "min_ms": min(s),
        "p50_ms": statistics.median(s),
        "p95_ms": s[int(len(s) * 0.95) - 1],
        "max_ms": max(s),
        "mean_ms": statistics.fmean(s),
    }
    print(
        f"  [{name:>28}]  n={len(s):>4}  "
        f"min={summary['min_ms']:7.3f}  "
        f"p50={summary['p50_ms']:7.3f}  "
        f"p95={summary['p95_ms']:7.3f}  "
        f"max={summary['max_ms']:7.3f}  "
        f"mean={summary['mean_ms']:7.3f} ms"
    )
    return summary


def make_mock_plugin(name: str, capabilities: list[str]) -> MagicMock:
    """Build a MagicMock that looks like a ProviderPlugin for the router."""
    mock = MagicMock(spec=ProviderPlugin)
    mock.name = name
    mock.capabilities = capabilities
    mock.health_check.return_value = True
    mock.chat.return_value = {
        "content": "OK",
        "model": "mock",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "raw": {},
    }
    return mock


_POOL_SPECS = [
    ("MiniMax-M3", ["中文", "推理", "工具調用", "辯論"]),
    ("GLM-5.3", ["中文", "程式碼", "推理", "工具調用", "辯論", "審查", "驗證"]),
    (
        "Google-Gemini",
        ["英文", "推理", "工具調用", "辯論", "Embedding", "長文", "審查", "驗證", "速度優先"],
    ),
    ("NVIDIA-Cloud", ["英文", "程式碼", "推理", "工具調用"]),
    (
        "DeepSeek-V4-Pro",
        ["中文", "英文", "程式碼", "推理", "工具調用", "辯論", "審查", "驗證"],
    ),
    ("DeepSeek-V4.1-Flash", ["中文", "速度優先"]),
    ("GLM-5.3-Flash", ["中文", "速度優先"]),
    ("OpenRouter-Free", ["英文", "速度優先"]),
    (
        "OpenAI-API",
        ["英文", "程式碼", "推理", "工具調用", "辯論", "審查", "驗證", "速度優先"],
    ),
    ("Claude-API", ["英文", "推理", "工具調用", "辯論", "審查", "驗證", "長文"]),
    ("Ollama-Mac", ["中文", "英文", "Embedding", "速度優先"]),
    ("Ollama-Surface", ["中文", "英文", "Embedding", "速度優先"]),
]


def synthetic_registry() -> tuple[MagicMock, dict[str, MagicMock]]:
    """Return ``(registry, plugins)`` with 12 mock pools wired up."""
    reg = MagicMock(spec=PluginRegistry)
    plugins = {name: make_mock_plugin(name, caps) for name, caps in _POOL_SPECS}
    reg.by_capability.side_effect = lambda cap: [
        p for p in plugins.values() if cap in p.capabilities
    ]
    reg.get.side_effect = lambda name: plugins.get(name)
    reg.names.return_value = [p.name for p in plugins.values()]
    return reg, plugins
