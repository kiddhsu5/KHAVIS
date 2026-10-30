#!/usr/bin/env python3
"""Run a deterministic, no-key demonstration of K.H.A.V.I.S. failover.

The demo uses the existing end-to-end stress harness with in-memory cloud,
webhook, and Ollama stand-ins. It never opens a listener and never calls a
real provider, so it is safe to run from a clean checkout or in CI.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

run_e2e_stress_test = importlib.import_module(
    "scripts.stress_test_failover_alerts"
).run_e2e_stress_test


_MIN_REQUESTS_FOR_FALLBACK = 8


def run_demo(requests: int = _MIN_REQUESTS_FOR_FALLBACK) -> dict[str, Any]:
    """Run the no-key failover flow and return a compact machine-readable summary."""
    if requests < 1:
        raise ValueError("requests must be at least 1")

    # The mock cloud pools need enough turns to exhaust every cloud candidate;
    # keep the public demo deterministic even when a caller asks for fewer.
    effective_requests = max(_MIN_REQUESTS_FOR_FALLBACK, requests)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        result = run_e2e_stress_test(num_requests=effective_requests, concurrency=1)

    local_fallbacks = int(result["local_fallbacks"])
    alerts = int(result["alerts_count"])
    summary: dict[str, Any] = {
        "demo": "zero-quota-failover",
        "requests": effective_requests,
        "successful_requests": int(result["successful_requests"]),
        "local_fallbacks": local_fallbacks,
        "alerts": alerts,
        "quota_exhausted": local_fallbacks > 0,
        "cloud_calls": int(result["cloud_calls"]),
        "local_calls": int(result["local_calls"]),
        "external_network": False,
        "api_keys_required": False,
        "ollama_service_required": False,
    }
    if summary["successful_requests"] != effective_requests:
        raise RuntimeError("demo did not complete every request")
    if local_fallbacks < 1:
        raise RuntimeError("demo did not activate the local fallback")
    if alerts < 1:
        raise RuntimeError("demo did not capture a webhook alert")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run K.H.A.V.I.S. zero-quota failover demo without API keys"
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=_MIN_REQUESTS_FOR_FALLBACK,
        help=f"Number of requests (minimum {_MIN_REQUESTS_FOR_FALLBACK} for deterministic fallback)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print only the machine-readable JSON summary",
    )
    args = parser.parse_args()
    summary = run_demo(args.requests)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return

    print("K.H.A.V.I.S. zero-quota failover demo")
    print("No API keys, external network, or Ollama service required")
    print(f"Requests recovered: {summary['successful_requests']}/{summary['requests']}")
    print(f"Local Ollama-style fallbacks: {summary['local_fallbacks']}")
    print(f"Captured webhook alerts: {summary['alerts']}")
    print("Result: quota exhaustion was detected and the task continued locally.")


if __name__ == "__main__":
    main()
