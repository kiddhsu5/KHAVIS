"""Real-time webhook alerting for K.H.A.V.I.S. quota and rate-limit events.

Dispatches alerts when pools hit low quota thresholds, encounter 429 rate limits,
trip circuit breakers, or when requests fall back to local Ollama models.
Features built-in debouncing/cooldown to prevent alert storms.

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

import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

#: Default alert cooldown per (pool, event_type) in seconds
DEFAULT_ALERT_COOLDOWN_S = 300.0
#: Max recent alerts retained in memory for dashboard display
MAX_ALERT_HISTORY = 100


@dataclass
class AlertEvent:
    """Structured event for a quota, circuit, or fallback alert."""

    pool_name: str
    event_type: (
        str  # "low_quota", "rate_limit_429", "circuit_open", "pool_exhausted", "local_fallback"
    )
    level: str  # "info", "warning", "error", "critical"
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_name": self.pool_name,
            "event_type": self.event_type,
            "level": self.level,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }


class AlertManager:
    """Coordinates alert dispatching, debouncing, and history tracking."""

    def __init__(
        self,
        webhook_url: str | None = None,
        cooldown_s: float = DEFAULT_ALERT_COOLDOWN_S,
        enabled: bool = True,
    ) -> None:
        self.webhook_url = webhook_url or os.environ.get("KHAVIS_WEBHOOK_URL")
        self.cooldown_s = cooldown_s
        self.enabled = enabled
        self._last_alert_time: dict[tuple[str, str], float] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=MAX_ALERT_HISTORY)
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="khavis-alert")

    @classmethod
    def from_config(cls, config_path: Path | str | None = None) -> AlertManager:
        """Load configuration from config/alerts.yaml or env vars."""
        from .env import config_dir  # noqa: PLC0415

        cfg_file = Path(config_path) if config_path else config_dir() / "alerts.yaml"
        data: dict[str, Any] = {}
        if cfg_file.exists():
            try:
                data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to parse %s: %r", cfg_file, exc)

        webhook = os.environ.get("KHAVIS_WEBHOOK_URL") or data.get("webhook_url")
        cooldown = float(
            os.environ.get(
                "KHAVIS_ALERT_COOLDOWN_S", data.get("cooldown_s", DEFAULT_ALERT_COOLDOWN_S)
            )
        )
        raw_enabled = (
            os.environ.get("KHAVIS_ALERTING", str(data.get("enabled", True))).strip().lower()
        )
        enabled = raw_enabled not in {"0", "false", "no", "off"}

        return cls(webhook_url=webhook, cooldown_s=cooldown, enabled=enabled)

    def _should_suppress(self, pool_name: str, event_type: str, now: float) -> bool:
        """Check if identical alert occurred recently within the cooldown window."""
        key = (pool_name, event_type)
        last_time = self._last_alert_time.get(key)
        if last_time is not None and (now - last_time) < self.cooldown_s:
            return True
        self._last_alert_time[key] = now
        return False

    def emit(
        self,
        pool_name: str,
        event_type: str,
        level: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        sync: bool = False,
    ) -> bool:
        """Emit an alert. Returns True if alert was dispatched, False if suppressed/disabled."""
        now = time.time()
        event = AlertEvent(
            pool_name=pool_name,
            event_type=event_type,
            level=level,
            message=message,
            details=details or {},
            timestamp=now,
        )

        with self._lock:
            # Store in history regardless of webhook delivery for dashboard visibility
            self._history.appendleft(event.to_dict())

            if not self.enabled:
                return False

            if self._should_suppress(pool_name, event_type, now):
                log.debug("Alert suppressed due to cooldown: %s (%s)", pool_name, event_type)
                return False

        if not self.webhook_url:
            log.info("[Alert] %s [%s] %s: %s", level.upper(), event_type, pool_name, message)
            return True

        if sync:
            return self._post_webhook(event)
        self._executor.submit(self._post_webhook, event)
        return True

    def _post_webhook(self, event: AlertEvent) -> bool:
        """HTTP POST payload formatted to suit generic webhooks, Slack, Discord, and Telegram."""
        if not self.webhook_url:
            return False

        payload: dict[str, Any] = {
            "khavis_alert": event.to_dict(),
            "text": f"[{event.level.upper()}] K.H.A.V.I.S. Alert: {event.pool_name} - {event.message}",
            "content": (
                f"**[K.H.A.V.I.S. Alert - {event.level.upper()}]** `{event.pool_name}`\n"
                f"> **Event:** `{event.event_type}`\n"
                f"> **Message:** {event.message}\n"
                f"> **Time:** <t:{int(event.timestamp)}:R>"
            ),
        }

        try:
            import requests  # noqa: PLC0415

            resp = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            if resp.status_code < 400:
                log.info("Alert webhook delivered successfully for %s", event.pool_name)
                return True
            log.warning(
                "Alert webhook delivery failed: HTTP %s (%s)", resp.status_code, resp.text[:200]
            )
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("Alert webhook exception for %s: %r", event.pool_name, exc)
            return False

    def recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent alert records for dashboard or monitoring APIs."""
        with self._lock:
            return list(self._history)[:limit]

    def clear(self) -> None:
        """Clear state and history (useful for test isolation)."""
        with self._lock:
            self._last_alert_time.clear()
            self._history.clear()


_MANAGER: AlertManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_alert_manager() -> AlertManager:
    """Return the global AlertManager singleton."""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = AlertManager.from_config()
    return _MANAGER


__all__ = [
    "AlertEvent",
    "AlertManager",
    "DEFAULT_ALERT_COOLDOWN_S",
    "get_alert_manager",
]
