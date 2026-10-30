"""Unit tests for active quota tracking and rate limit header parsing.

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

import time
from unittest.mock import MagicMock, patch

from core.health import HealthPolicy, PoolHealthTracker
from core.quota_tracker import (
    QuotaTracker,
    RateLimitSnapshot,
    get_quota_tracker,
    parse_rate_limit_headers,
    parse_reset_offset,
    query_upstream_balance,
)


class TestResetOffsetParser:
    def test_parses_seconds(self):
        assert parse_reset_offset("12.5") == 12.5
        assert parse_reset_offset(10) == 10.0

    def test_parses_durations(self):
        assert parse_reset_offset("500ms") == 0.5
        assert parse_reset_offset("20ms") == 0.02
        assert parse_reset_offset("15s") == 15.0
        assert parse_reset_offset("2m") == 120.0
        assert parse_reset_offset("1h") == 3600.0

    def test_parses_epoch_timestamp(self):
        now = time.time()
        future = now + 42.0
        parsed = parse_reset_offset(str(future), now=now)
        assert parsed is not None
        assert abs(parsed - 42.0) < 0.1

    def test_invalid_and_empty(self):
        assert parse_reset_offset(None) is None
        assert parse_reset_offset("") is None
        assert parse_reset_offset("invalid_string") is None


class TestHeaderParsing:
    def test_openai_headers(self):
        headers = {
            "x-ratelimit-remaining-requests": "45",
            "x-ratelimit-limit-requests": "500",
            "x-ratelimit-reset-requests": "250ms",
            "x-ratelimit-remaining-tokens": "180000",
            "x-ratelimit-limit-tokens": "200000",
            "x-ratelimit-reset-tokens": "6s",
        }
        snap = parse_rate_limit_headers(headers, pool_name="OpenAI-GPT4")
        assert snap.pool_name == "OpenAI-GPT4"
        assert snap.remaining_requests == 45
        assert snap.limit_requests == 500
        assert snap.reset_requests_s == 0.25
        assert snap.remaining_tokens == 180000
        assert snap.limit_tokens == 200000
        assert snap.reset_tokens_s == 6.0
        assert not snap.is_exhausted()

    def test_anthropic_headers(self):
        headers = {
            "anthropic-ratelimit-requests-remaining": "5",
            "anthropic-ratelimit-requests-limit": "100",
            "anthropic-ratelimit-requests-reset": "2026-10-15T04:00:00Z",
            "anthropic-ratelimit-tokens-remaining": "50",
            "anthropic-ratelimit-tokens-limit": "40000",
            "anthropic-ratelimit-tokens-reset": "15s",
        }
        snap = parse_rate_limit_headers(headers, pool_name="Claude-Sonnet")
        assert snap.remaining_requests == 5
        assert snap.remaining_tokens == 50
        assert snap.reset_tokens_s == 15.0
        # Tokens are 50, which is below DEFAULT_TOKEN_BUFFER (150)
        assert snap.is_exhausted()

    def test_exhaustion_detection(self):
        now = 1000.0
        # Normal
        snap = RateLimitSnapshot(
            pool_name="TestPool",
            remaining_requests=10,
            remaining_tokens=5000,
            updated_at=now,
        )
        assert not snap.is_exhausted(now=now)

        # Requests exhausted
        snap.remaining_requests = 1
        snap.reset_requests_s = 30.0
        assert snap.is_exhausted(request_buffer=1, now=now)
        # After reset time elapses
        assert not snap.is_exhausted(request_buffer=1, now=now + 31.0)

        # Tokens exhausted
        snap.remaining_requests = 10
        snap.remaining_tokens = 50
        snap.reset_tokens_s = 10.0
        assert snap.is_exhausted(token_buffer=100, now=now)
        assert not snap.is_exhausted(token_buffer=100, now=now + 11.0)

        # Negative/zero balance
        snap.remaining_tokens = 5000
        snap.balance_usd = 0.0
        assert snap.is_exhausted(now=now)


class TestQuotaTracker:
    def test_record_and_merge_headers(self):
        tracker = QuotaTracker()
        # First header: only request limits
        tracker.record_headers("pool-a", {"x-ratelimit-remaining-requests": "10"})
        snap = tracker.get_snapshot("pool-a")
        assert snap is not None
        assert snap.remaining_requests == 10
        assert snap.remaining_tokens is None

        # Second header: only token limits
        tracker.record_headers("pool-a", {"x-ratelimit-remaining-tokens": "2000"})
        snap2 = tracker.get_snapshot("pool-a")
        assert snap2.remaining_requests == 10
        assert snap2.remaining_tokens == 2000

    def test_record_api_result(self):
        tracker = QuotaTracker()
        tracker.record_api_result(
            "openrouter-free",
            {"balance_usd": 0.0, "is_free_tier": True, "total_tokens": 100000},
        )
        assert tracker.is_pool_exhausted("openrouter-free")
        snap = tracker.get_snapshot("openrouter-free")
        assert snap.is_free_tier is True
        assert snap.balance_usd == 0.0

    @patch("requests.get")
    def test_query_upstream_balance_openrouter(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "label": "my-key",
                "usage": 2.5,
                "limit": 10.0,
                "is_free_tier": False,
            }
        }
        mock_get.return_value = mock_resp

        plugin = MagicMock()
        plugin.default_endpoint = "https://openrouter.ai/api/v1"
        plugin.api_key = "sk-or-test"

        res = query_upstream_balance(plugin)
        assert res is not None
        assert res["balance_usd"] == 7.5
        assert res["usage_usd"] == 2.5
        assert res["is_free_tier"] is False


class TestHealthTrackerIntegration:
    def test_exhausted_quota_blocks_pool_availability(self):
        tracker = PoolHealthTracker(HealthPolicy(respect_quota=True))
        qt = get_quota_tracker()
        qt.clear()

        # Closed pool normally available
        assert tracker.is_available("PoolA") is True

        # Now mark it exhausted in quota tracker
        qt.record_headers(
            "PoolA",
            {
                "x-ratelimit-remaining-requests": "0",
                "x-ratelimit-reset-requests": "60s",
            },
        )
        assert qt.is_pool_exhausted("PoolA") is True
        assert tracker.is_available("PoolA") is False
        qt.clear()
