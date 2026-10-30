#!/usr/bin/env python3
"""Integration-test runner: pings every pool with a minimal prompt.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

This script hits the 10 LLM provider endpoints configured for
``khavis`` with a tiny "ping" prompt (max_tokens=5) and prints a
colour-coded pass/fail report. Pools whose required API keys are
missing are skipped with a warning rather than failed.

Run::

    python scripts/integration_test.py                # all pools
    python scripts/integration_test.py --only MiniMax-M3
    python scripts/integration_test.py --verbose

Exit code is ``0`` if every pool that *could* run passed; otherwise
``1``. A pool that is *skipped* does not influence the exit code.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure the project root is importable when invoked directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env (SURFACE_IP, *API_KEY, ...) before plugins are constructed.
from core.env import load_env  # noqa: E402

load_env()
from providers.base import ProviderPlugin  # noqa: E402
from providers.claude import AnthropicPlugin  # noqa: E402
from providers.gemini import GeminiPlugin  # noqa: E402
from providers.glm import GLMPlugin  # noqa: E402
from providers.MiniMax import MiniMaxPlugin  # noqa: E402
from providers.nvidia import NvidiaPlugin  # noqa: E402
from providers.ollama import OllamaPlugin  # noqa: E402
from providers.openai import OpenAIPlugin  # noqa: E402
from providers.openrouter import OpenRouterPlugin  # noqa: E402
from providers.volcano import (  # noqa: E402
    VolcanoPluginFactory,
)

# ---------------------------------------------------------------------------
# ANSI colour helpers (auto-disabled when stdout is not a TTY)
# ---------------------------------------------------------------------------
_USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _wrap(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def green(text: str) -> str:
    return _wrap("32", text)


def red(text: str) -> str:
    return _wrap("31", text)


def yellow(text: str) -> str:
    return _wrap("33", text)


def cyan(text: str) -> str:
    return _wrap("36", text)


def bold(text: str) -> str:
    return _wrap("1", text)


# ---------------------------------------------------------------------------
# Pool → factory wiring
# ---------------------------------------------------------------------------
@dataclass
class PoolSpec:
    """Static description of a single pool to test."""

    name: str
    env_key: str | None
    factory: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    requires_api_key: bool = True


def _build_pools() -> list[PoolSpec]:
    """Build the ordered list of pools to test."""
    pools: list[PoolSpec] = [
        PoolSpec(
            name="MiniMax-M3",
            env_key="MiniMax_API_KEY",
            factory=lambda: MiniMaxPlugin(),
        ),
        PoolSpec(
            name="GLM-5.3",
            env_key="ZHIPUAI_API_KEY",
            factory=lambda: GLMPlugin(),
        ),
        PoolSpec(
            name="Google-Gemini-Flash",
            env_key="GOOGLE_API_KEY",
            factory=lambda: GeminiPlugin(model="gemini-flash-latest"),
            metadata={"variants": "gemini-flash-latest,gemini-pro-latest"},
        ),
        PoolSpec(
            name="Google-Gemini-Pro",
            env_key="GOOGLE_API_KEY",
            factory=lambda: GeminiPlugin(model="gemini-pro-latest"),
            metadata={"variants": "gemini-flash-latest,gemini-pro-latest"},
        ),
        PoolSpec(
            name="NVIDIA-Cloud",
            env_key="NVIDIA_API_KEY",
            factory=lambda: NvidiaPlugin(),
        ),
        PoolSpec(
            name="DeepSeek-V4-Pro",
            env_key="BYTEDANCE_API_KEY",
            factory=lambda: VolcanoPluginFactory()[0],
        ),
        PoolSpec(
            name="DeepSeek-V4.1-Flash",
            env_key="BYTEDANCE_API_KEY",
            factory=lambda: VolcanoPluginFactory()[1],
        ),
        PoolSpec(
            name="GLM-5.3-Flash",
            env_key="BYTEDANCE_API_KEY",
            factory=lambda: VolcanoPluginFactory()[2],
        ),
        PoolSpec(
            name="OpenRouter-Free",
            env_key="OPENROUTER_API_KEY",
            factory=lambda: OpenRouterPlugin(),
        ),
        PoolSpec(
            name="OpenAI-API",
            env_key="OPENAI_API_KEY",
            factory=lambda: OpenAIPlugin(),
            metadata={"tier": "platform_api"},
        ),
        PoolSpec(
            name="Claude-API",
            env_key="ANTHROPIC_API_KEY",
            factory=lambda: AnthropicPlugin(),
            metadata={"tier": "platform_api"},
        ),
        PoolSpec(
            name="Ollama-Mac",
            env_key=None,
            factory=lambda: OllamaPlugin(name="Ollama-Mac", model="gemma4:e2b"),
            requires_api_key=False,
        ),
        PoolSpec(
            name="Ollama-Surface",
            env_key="SURFACE_IP",
            factory=lambda: OllamaPlugin(name="Ollama-Surface", model="qwen2.5:1.5b"),
            requires_api_key=False,
        ),
    ]
    return pools


# ---------------------------------------------------------------------------
# Per-pool execution
# ---------------------------------------------------------------------------
PING_MESSAGES = [
    {"role": "system", "content": "You are a connectivity probe."},
    {"role": "user", "content": "Reply with the single word: pong"},
]


@dataclass
class PoolResult:
    name: str
    status: str  # "pass" | "fail" | "skip"
    duration_s: float
    detail: str = ""
    response: dict[str, Any] | None = None
    error: str | None = None


def _missing_key(spec: PoolSpec) -> bool:
    """Return True when the pool's required env var is unset."""
    if not spec.requires_api_key:
        return False
    return not (spec.env_key and os.getenv(spec.env_key))


def _run_pool(spec: PoolSpec, verbose: bool = False) -> PoolResult:
    """Instantiate ``spec`` and invoke ``chat`` with a tiny ping prompt."""
    started = time.perf_counter()
    if _missing_key(spec):
        return PoolResult(
            name=spec.name,
            status="skip",
            duration_s=0.0,
            detail=f"missing env var {spec.env_key}",
        )

    try:
        plugin: ProviderPlugin = spec.factory()
    except Exception as exc:  # pragma: no cover - defensive
        return PoolResult(
            name=spec.name,
            status="fail",
            duration_s=time.perf_counter() - started,
            error=f"instantiate failed: {exc!r}",
        )

    try:
        response = plugin.chat(
            list(PING_MESSAGES),
            max_tokens=5,
            temperature=0.0,
        )
    except Exception as exc:
        return PoolResult(
            name=spec.name,
            status="fail",
            duration_s=time.perf_counter() - started,
            error=f"chat() raised: {exc!r}",
        )

    duration = time.perf_counter() - started
    return PoolResult(
        name=spec.name,
        status="pass",
        duration_s=duration,
        response=response,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _format_status(result: PoolResult) -> str:
    if result.status == "pass":
        return green("  PASS")
    if result.status == "skip":
        return yellow("  SKIP")
    return red("  FAIL")


def _summarise_response(response: dict[str, Any]) -> str:
    """Pull a one-line preview out of a normalised chat response."""
    try:
        choice = response["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return "<unparseable response>"
    preview = content.strip().replace("\n", " ")
    return preview[:60] + ("..." if len(preview) > 60 else "")


def _print_results(results: list[PoolResult], verbose: bool) -> None:
    print(bold("\nIntegration test results"))
    print("=" * 72)
    for result in results:
        status = _format_status(result)
        if result.status == "skip":
            print(f"{status}  {result.name:<24} {yellow(result.detail)}")
            continue
        if result.status == "fail":
            print(f"{status}  {result.name:<24} {red(result.error or '')}")
            continue

        preview = _summarise_response(result.response or {})
        ms = result.duration_s * 1000
        print(
            f"{status}  {result.name:<24} "
            f"{cyan(f'{ms:6.0f} ms')}  "
            f"model={result.response.get('model', '?') if result.response else '?'}  "
            f"reply={preview!r}"
        )
        if verbose and result.response is not None:
            print(f"        usage={result.response.get('usage', {})}")
    print("=" * 72)


def _print_summary(results: list[PoolResult], total_s: float) -> None:
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    skipped = sum(1 for r in results if r.status == "skip")
    print(
        f"{bold('Summary')}: {green(str(passed) + ' passed')}, "
        f"{red(str(failed) + ' failed')}, "
        f"{yellow(str(skipped) + ' skipped')} "
        f"(elapsed {total_s:.2f}s)"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ping every K.H.A.V.I.S. pool with a minimal prompt.",
    )
    parser.add_argument(
        "--only",
        metavar="POOL",
        help="Only test the named pool (matches PoolSpec.name, case-sensitive).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full usage data for passing pools.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colour output (also honoured via NO_COLOR env).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _USE_COLOR
    args = _parse_args(argv)
    if args.no_color:
        _USE_COLOR = False

    pools = _build_pools()
    if args.only:
        pools = [p for p in pools if p.name == args.only]
        if not pools:
            print(red(f"unknown pool: {args.only}"), file=sys.stderr)
            return 1

    print(bold(f"Pinging {len(pools)} pool(s)..."))
    results: list[PoolResult] = []
    overall = time.perf_counter()
    for spec in pools:
        results.append(_run_pool(spec, verbose=args.verbose))
    elapsed = time.perf_counter() - overall

    _print_results(results, args.verbose)
    _print_summary(results, elapsed)

    failed = sum(1 for r in results if r.status == "fail")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
