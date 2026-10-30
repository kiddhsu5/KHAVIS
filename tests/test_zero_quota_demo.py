"""Regression tests for the no-key zero-quota demo."""

from __future__ import annotations

from examples.zero_quota_demo import run_demo


def test_zero_quota_demo_recovers_locally_and_captures_alerts() -> None:
    summary = run_demo(requests=1)

    assert summary["requests"] == 8
    assert summary["successful_requests"] == 8
    assert summary["local_fallbacks"] >= 1
    assert summary["alerts"] >= 1
    assert summary["quota_exhausted"] is True
    assert summary["api_keys_required"] is False
    assert summary["external_network"] is False
    assert summary["ollama_service_required"] is False
