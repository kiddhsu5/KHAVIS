"""Unit tests for Webhook alerting and cooldown debouncing.

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

from unittest.mock import MagicMock, patch

from core.alerting import AlertEvent, AlertManager


class TestAlertEvent:
    def test_serialization(self):
        ev = AlertEvent(
            pool_name="OpenAI-API",
            event_type="rate_limit_429",
            level="error",
            message="429 Too Many Requests",
            details={"retry_after": 30},
        )
        d = ev.to_dict()
        assert d["pool_name"] == "OpenAI-API"
        assert d["event_type"] == "rate_limit_429"
        assert d["level"] == "error"
        assert d["details"]["retry_after"] == 30


class TestAlertManager:
    def test_cooldown_suppression(self):
        mgr = AlertManager(webhook_url=None, cooldown_s=10.0, enabled=True)
        # First alert goes through
        assert (
            mgr.emit(
                "PoolA",
                "rate_limit_429",
                "error",
                "first fail",
                sync=True,
            )
            is True
        )

        # Immediate second identical alert is suppressed by cooldown
        assert (
            mgr.emit(
                "PoolA",
                "rate_limit_429",
                "error",
                "second fail",
                sync=True,
            )
            is False
        )

        # Different pool is not suppressed
        assert (
            mgr.emit(
                "PoolB",
                "rate_limit_429",
                "error",
                "pool b fail",
                sync=True,
            )
            is True
        )

        # Different event type on same pool is not suppressed
        assert (
            mgr.emit(
                "PoolA",
                "circuit_open",
                "critical",
                "circuit open",
                sync=True,
            )
            is True
        )

    @patch("requests.post")
    def test_webhook_post_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        mgr = AlertManager(webhook_url="https://hooks.example.com/khavis", cooldown_s=60.0)
        res = mgr.emit(
            "MiniMax-M3",
            "low_quota",
            "warning",
            "Only 1 request remaining",
            {"rem": 1},
            sync=True,
        )
        assert res is True
        assert mock_post.called
        kwargs = mock_post.call_args[1]
        payload = kwargs["json"]
        assert payload["khavis_alert"]["pool_name"] == "MiniMax-M3"
        assert "text" in payload
        assert "content" in payload

    def test_recent_alerts_history(self):
        mgr = AlertManager(enabled=True)
        mgr.clear()
        mgr.emit("P1", "e1", "info", "msg 1", sync=True)
        mgr.emit("P2", "e2", "warning", "msg 2", sync=True)
        recent = mgr.recent_alerts()
        assert len(recent) == 2
        assert recent[0]["pool_name"] == "P2"  # Newest first
        assert recent[1]["pool_name"] == "P1"
