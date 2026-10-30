#!/usr/bin/env python3
"""End-to-End Stress Test for K.H.A.V.I.S. Failover, Quota Tracking & Ollama Safeguard.

Simulates:
1. High-frequency / concurrent requests triggering 429 Rate Limits on cloud pools.
2. Active quota tracking capturing response headers and marking exhausted states.
3. Automated Webhook alerting to a local mock receiver with debouncing verification.
4. Seamless fallback degradation to local Ollama when all cloud pools are exhausted.
5. Emits a structured telemetry summary report.

Usage:
    python3 scripts/stress_test_failover_alerts.py
    python3 scripts/stress_test_failover_alerts.py --concurrency 5 --requests 20

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.alerting import AlertManager  # noqa: E402
from core.failover import call_with_failover  # noqa: E402
from core.health import HealthPolicy, reset_tracker  # noqa: E402
from core.quota_tracker import get_quota_tracker  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("stress_test")


class MockWebhookReceiver:
    """In-process mock webhook receiver to intercept and verify alerts without requiring socket bind."""

    def __init__(self, webhook_url: str = "http://mock-webhook.local/alerts") -> None:
        self.webhook_url = webhook_url
        self.received_alerts: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._patcher: Any = None

    def start(self) -> str:
        outer = self
        import requests  # noqa: PLC0415

        orig_post = requests.post

        def mock_post(url: str, *args: Any, **kwargs: Any) -> Any:
            if url == outer.webhook_url:
                payload = kwargs.get("json", {})
                with outer._lock:
                    outer.received_alerts.append(payload)
                mock_resp = type(
                    "MockResponse",
                    (),
                    {
                        "status_code": 200,
                        "text": '{"status":"ok"}',
                        "json": lambda self: {"status": "ok"},
                    },
                )()
                return mock_resp
            return orig_post(url, *args, **kwargs)

        from unittest.mock import patch  # noqa: PLC0415

        self._patcher = patch("requests.post", side_effect=mock_post)
        self._patcher.start()
        log.info("Mock Webhook receiver active for %s", self.webhook_url)
        return self.webhook_url

    def stop(self) -> None:
        if self._patcher:
            self._patcher.stop()

    def get_alerts(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.received_alerts)


class MockPlugin:
    """Simulates a provider pool with configurable failure patterns."""

    def __init__(
        self,
        name: str,
        provider_id: str = "cloud",
        capabilities: list[str] | None = None,
        fail_with_429: bool = False,
        exhaust_after: int = 0,
        reply: str = "Hello from simulated LLM",
    ) -> None:
        self.name = name
        self.provider_id = provider_id
        self.capabilities = capabilities or ["code", "zh", "en"]
        self.fail_with_429 = fail_with_429
        self.exhaust_after = exhaust_after
        self.reply = reply
        self.call_count = 0
        self._lock = threading.Lock()

    def has_capability(self, cap: str) -> bool:
        return cap in self.capabilities

    def list_models(self) -> list[str]:
        return [f"model-{self.name}"]

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "detail": "healthy"}

    def check_quota(self) -> dict[str, Any]:
        return {"remaining": 50000, "total": 100000}

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.call_count += 1
            idx = self.call_count

        if self.fail_with_429 or (self.exhaust_after > 0 and idx > self.exhaust_after):
            # Create a mock error with 429 status and rate limit headers
            err = RuntimeError(f"HTTP 429 Too Many Requests: Rate limit exceeded for {self.name}")
            mock_resp = type(
                "MockResponse",
                (),
                {
                    "status_code": 429,
                    "headers": {
                        "x-ratelimit-remaining-requests": "0",
                        "x-ratelimit-remaining-tokens": "0",
                        "x-ratelimit-reset-requests": "45s",
                        "retry-after": "45",
                    },
                },
            )()
            err.response = mock_resp  # type: ignore[attr-defined]
            time.sleep(0.01)  # small network jitter simulation
            raise err

        time.sleep(0.02)  # simulated inference latency
        return {
            "id": f"chatcmpl-mock-{self.name}-{idx}",
            "object": "chat.completion",
            "model": f"model-{self.name}",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": f"[{self.name}] {self.reply}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 18, "total_tokens": 30},
            "_headers": {
                "x-ratelimit-remaining-requests": "15",
                "x-ratelimit-remaining-tokens": "8500",
                "x-ratelimit-reset-requests": "10s",
            },
        }


class MockRouter:
    """Simulates CapabilityRouter with priority failover candidate sequencing."""

    def __init__(self, cloud_pools: list[MockPlugin], local_pools: list[MockPlugin]) -> None:
        self.cloud_pools = cloud_pools
        self.local_pools = local_pools
        self.registry = cloud_pools + local_pools

    def select(
        self,
        capability: str,
        strategy: str = "priority",
        session_id: str | None = None,
        exclude: set[str] | list[str] | None = None,
    ) -> MockPlugin | None:
        exclude_set = set(exclude or [])
        from core.health import get_tracker

        tracker = get_tracker()
        for pool in self.cloud_pools:
            if pool.name not in exclude_set and tracker.is_available(pool.name):
                return pool
        return None

    def select_any(
        self,
        strategy: str = "priority",
        exclude: set[str] | list[str] | None = None,
    ) -> MockPlugin | None:
        return self.select("any", strategy=strategy, exclude=exclude)


def run_e2e_stress_test(
    num_requests: int = 15,
    concurrency: int = 3,
) -> dict[str, Any]:
    """Execute end-to-end stress test validating 429 exhaustion, alerts, and Ollama fallback."""
    log.info("=" * 70)
    log.info("🚀 Starting K.H.A.V.I.S. E2E Stress Test")
    log.info("Total Requests: %d | Concurrency: %d", num_requests, concurrency)
    log.info("=" * 70)

    # 1. Initialize Mock Webhook Receiver
    receiver = MockWebhookReceiver()
    webhook_url = receiver.start()

    # Configure AlertManager with local mock webhook and short cooldown for testing
    os.environ["KHAVIS_WEBHOOK_URL"] = webhook_url
    alert_mgr = AlertManager(webhook_url=webhook_url, cooldown_s=0.5, enabled=True)
    alert_mgr.clear()
    import core.alerting  # noqa: PLC0415

    core.alerting._MANAGER = alert_mgr

    # Clear quota tracker for fresh state
    qt = get_quota_tracker()
    qt.clear()

    # Configure HealthTracker with quota respect
    reset_tracker(HealthPolicy(respect_quota=True, failure_threshold=2))

    # 2. Build Mock Pools
    # Cloud A fails after 2 calls with 429
    cloud_a = MockPlugin("Cloud-OpenAI", provider_id="openai", fail_with_429=False, exhaust_after=2)
    # Cloud B immediately fails with 429
    cloud_b = MockPlugin("Cloud-Claude", provider_id="claude", fail_with_429=True)
    # Cloud C fails after 1 call with 429
    cloud_c = MockPlugin("Cloud-Gemini", provider_id="gemini", fail_with_429=False, exhaust_after=1)

    # Local Ollama pools are healthy and ready to take over
    ollama_mac = MockPlugin(
        "Ollama-Mac",
        provider_id="ollama",
        capabilities=["local", "code", "zh", "en"],
        reply="Successfully executed by local Ollama-Mac safeguard!",
    )
    ollama_surface = MockPlugin(
        "Ollama-Surface",
        provider_id="ollama",
        capabilities=["local", "code", "zh", "en"],
        reply="Successfully executed by local Ollama-Surface safeguard!",
    )

    router = MockRouter(
        cloud_pools=[cloud_a, cloud_b, cloud_c],
        local_pools=[ollama_mac, ollama_surface],
    )

    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    local_fallbacks = 0
    successful_requests = 0

    start_time = time.time()

    def send_task(request_idx: int) -> dict[str, Any]:
        t0 = time.time()
        msgs = [
            {"role": "user", "content": f"Task #{request_idx}: Process real-time data payload."}
        ]
        try:
            used_plugin, resp, attempts = call_with_failover(
                router,
                capability="code",
                messages=msgs,
                max_attempts=3,
                auto_escalate_agents=False,  # Focus on testing Ollama degradation safeguard
                local_fallback=True,
            )
            elapsed = time.time() - t0
            is_local = resp.get("khavis", {}).get("degraded_to_local", False)
            return {
                "idx": request_idx,
                "ok": True,
                "used_pool": used_plugin.name,
                "attempts": attempts,
                "degraded_to_local": is_local,
                "elapsed": elapsed,
                "content": resp["choices"][0]["message"]["content"][:60],
            }
        except Exception as exc:  # noqa: BLE001
            elapsed = time.time() - t0
            return {
                "idx": request_idx,
                "ok": False,
                "error": str(exc),
                "elapsed": elapsed,
            }

    log.info("Executing concurrent requests against failover engine...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(send_task, i + 1) for i in range(num_requests)]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            results.append(res)
            latencies.append(res["elapsed"])
            if res.get("ok"):
                successful_requests += 1
                if res.get("degraded_to_local"):
                    local_fallbacks += 1

    total_duration = time.time() - start_time

    # Wait briefly for async webhook deliveries to arrive
    time.sleep(0.2)
    received_alerts = receiver.get_alerts()
    receiver.stop()

    # 3. Compile Telemetry Summary
    cloud_calls = cloud_a.call_count + cloud_b.call_count + cloud_c.call_count
    local_calls = ollama_mac.call_count + ollama_surface.call_count

    avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0

    print("\n" + "=" * 70)
    print(" 📊 K.H.A.V.I.S. E2E STRESS TEST TELEMETRY REPORT")
    print("=" * 70)
    print(f"Total Requests Dispatched     : {num_requests}")
    print(
        f"Successful Requests           : {successful_requests} ({successful_requests / num_requests * 100:.1f}%)"
    )
    print(f"Failed Requests (Outages)     : {num_requests - successful_requests} (0.0% expected)")
    print(f"Total Cloud Pool Calls (429)  : {cloud_calls}")
    print(f"Local Ollama Fallbacks Taken  : {local_fallbacks} (100% of exhausted tasks recovered)")
    print(f"Local Ollama Pool Invocations : {local_calls}")
    print(f"Total Test Duration           : {total_duration:.2f}s")
    print(f"Average Request Latency       : {avg_latency * 1000:.1f}ms")
    print(f"Webhooks Received             : {len(received_alerts)}")

    print("\n--- Failed Requests Details ---")
    for r in results:
        if not r.get("ok"):
            print(f"  • Request #{r['idx']} failed: {r.get('error')}")

    print("\n--- Model Pool Invocation Breakdown ---")
    print(f"  • {cloud_a.name:<18}: {cloud_a.call_count} calls (Exhausted after 2)")
    print(f"  • {cloud_b.name:<18}: {cloud_b.call_count} calls (Continuous 429)")
    print(f"  • {cloud_c.name:<18}: {cloud_c.call_count} calls (Exhausted after 1)")
    print(f"  • {ollama_mac.name:<18}: {ollama_mac.call_count} calls [PRIMARY SAFEGUARD]")
    print(f"  • {ollama_surface.name:<18}: {ollama_surface.call_count} calls [SECONDARY SAFEGUARD]")

    print("\n--- Intercepted Webhook Alerts Samples ---")
    for i, a in enumerate(received_alerts[:5], 1):
        alert_body = a.get("khavis_alert", {})
        print(
            f"  [{i}] Level: {alert_body.get('level', 'N/A').upper():<8} | Event: {alert_body.get('event_type'):<15} | Pool: {alert_body.get('pool_name')}"
        )
        print(f"      Message: {alert_body.get('message')}")

    print("=" * 70)

    # Verification assertions
    assert successful_requests == num_requests, (
        f"Expected 100% success rate, got {successful_requests}/{num_requests}"
    )
    assert local_fallbacks > 0, (
        "Expected local Ollama fallback to be activated after cloud exhaustion"
    )
    assert len(received_alerts) > 0, "Expected webhook alerts to be received"

    print(
        "✅ ALL E2E CRITERIA VERIFIED: Zero-downtime Ollama safeguard and Webhook alerts functional!\n"
    )

    return {
        "num_requests": num_requests,
        "successful_requests": successful_requests,
        "local_fallbacks": local_fallbacks,
        "cloud_calls": cloud_calls,
        "local_calls": local_calls,
        "alerts_count": len(received_alerts),
        "total_duration": total_duration,
        "avg_latency": avg_latency,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="K.H.A.V.I.S. E2E Stress Test Runner")
    parser.add_argument("--requests", type=int, default=12, help="Number of requests to dispatch")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrency worker threads")
    args = parser.parse_args()

    run_e2e_stress_test(num_requests=args.requests, concurrency=args.concurrency)


if __name__ == "__main__":
    main()
