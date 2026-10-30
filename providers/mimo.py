"""MiMo Code provider plugin (Xiaomi MiMo token plan, Anthropic-compatible).

The MiMo token plan exposes the Anthropic Messages API at
``https://token-plan-cn.xiaomimimo.com/anthropic`` and authenticates with
a bearer token read from ``MIMO_AUTH_TOKEN`` (or passed as ``api_key``).
There is no OpenAI-compatible surface, so this plugin speaks Messages
directly over ``requests`` — no extra SDK dependency.

``ANTHROPIC_AUTH_TOKEN`` is deliberately *not* used as a fallback: on a
machine running Claude Code that variable may hold an Anthropic token, and
silently sending it to a third-party endpoint would leak it. Set
``MIMO_AUTH_TOKEN`` explicitly (it may hold the same value if your Claude
Code is itself pointed at the MiMo relay).

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

import os
from typing import Any

import requests

from ._anthropic_format import extract_text, raise_if_empty, to_anthropic_messages, usage_from
from ._common import resolve_timeout
from .base import ProviderPlugin

DEFAULT_ENDPOINT = "https://token-plan-cn.xiaomimimo.com/anthropic"
DEFAULT_MODEL = "mimo-v2.6-pro"
ENV_KEY = "MIMO_AUTH_TOKEN"
ANTHROPIC_VERSION = "2023-06-01"


class MimoCodePlugin(ProviderPlugin):
    """MiMo Code pool served through the Anthropic Messages API."""

    provider_id = "mimo"

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        token = api_key if api_key is not None else os.getenv(ENV_KEY)
        super().__init__(
            name="MiMo-Code",
            endpoint=endpoint if endpoint is not None else DEFAULT_ENDPOINT,
            api_key=token,
            model=model if model is not None else DEFAULT_MODEL,
            capabilities=capabilities
            or [
                "中文",
                "英文",
                "程式碼",
                "推理",
                "工具調用",
                "長文",
            ],
            metadata=metadata or {"region": "cn", "tier": "token_plan"},
        )

    # ------------------------------------------------------------------
    # ProviderPlugin interface
    # ------------------------------------------------------------------
    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("MiMo-Code: missing MIMO_AUTH_TOKEN")
        model = kwargs.pop("model", None) or self.model
        # Transport option, not a Messages API field — must not reach the payload.
        timeout = resolve_timeout(self.metadata, kwargs.pop("timeout", None), default=120)
        system_text, converted = to_anthropic_messages(messages)

        payload: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": kwargs.pop("max_tokens", 1024),
        }
        if system_text is not None:
            payload["system"] = system_text
        payload.update({k: v for k, v in kwargs.items() if k != "temperature"})

        resp = requests.post(
            f"{self.default_endpoint.rstrip('/')}/v1/messages",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        if hasattr(resp, "headers") and resp.headers:
            try:
                from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

                get_quota_tracker().record_headers(self.name, resp.headers)
            except Exception:  # noqa: BLE001
                pass
        resp.raise_for_status()
        data = resp.json()
        out = self._normalize(data)
        raise_if_empty(
            out["choices"][0]["message"]["content"],
            data.get("content") if isinstance(data, dict) else None,
            data.get("stop_reason") if isinstance(data, dict) else None,
            self.name,
        )
        return out

    def check_quota(self) -> dict[str, Any]:
        return {
            "remaining": "unknown",
            "total": "unknown",
            "tier": self.metadata.get("tier"),
            "provider": self.provider_id,
            "model": self.model,
            "note": "MiMo token plan quota is visible in the token-plan console.",
        }

    def list_models(self) -> list[str]:
        return [self.model or DEFAULT_MODEL, "mimo-v2.6-pro"]

    def health_check(self) -> dict[str, Any]:
        return {
            "ok": bool(self.api_key) and bool(self.default_endpoint) and bool(self.model),
            "detail": (
                f"endpoint={self.default_endpoint} "
                f"model={self.model} key_present={bool(self.api_key)}"
            ),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Coerce an Anthropic Messages response into the OpenAI-style dict.

        MiMo returns ``thinking`` content blocks alongside ``text`` ones;
        only ``text`` is surfaced as the assistant message.
        """
        return {
            "choices": [
                {"message": {"content": extract_text(data.get("content")), "role": "assistant"}}
            ],
            "model": data.get("model") or self.model,
            "usage": usage_from(data.get("usage") or {}),
        }
