"""Anthropic Claude API provider plugin.

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

from ._anthropic_format import extract_text, raise_if_empty, to_anthropic_messages, usage_from
from ._common import resolve_timeout
from .base import ProviderPlugin

try:
    from anthropic import Anthropic
except Exception:  # pragma: no cover - import guard
    Anthropic = None  # type: ignore[assignment,misc]


DEFAULT_ENDPOINT = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
ENV_KEY = "ANTHROPIC_API_KEY"


class AnthropicPlugin(ProviderPlugin):
    """Anthropic Claude API pool (Sonnet 5 / Opus 4.5 / Haiku 4.5)."""

    provider_id = "claude"

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name="Claude-API",
            endpoint=endpoint if endpoint is not None else DEFAULT_ENDPOINT,
            api_key=api_key if api_key is not None else os.getenv(ENV_KEY),
            model=model if model is not None else DEFAULT_MODEL,
            capabilities=capabilities
            or [
                "英文",
                "推理",
                "工具調用",
                "長文",
            ],
            metadata=metadata or {"region": "us", "tier": "platform"},
        )
        self._client: Any = None

    def _get_client(self) -> Any:
        if Anthropic is None:
            raise RuntimeError("anthropic package is not installed")
        if self._client is None:
            self._client = Anthropic(
                api_key=self.api_key or "MISSING_API_KEY",
                base_url=self.default_endpoint,
                timeout=resolve_timeout(self.metadata),
                # Retries belong to the failover layer, not the SDK.
                max_retries=0,
            )
        return self._client

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        """Send a chat completion request via the Anthropic Messages API.

        Translates the OpenAI-style ``messages`` shape (system + alternating
        user/assistant turns) into Anthropic's ``system`` + ``messages``
        fields, then normalises the response back to the canonical dict
        expected by the router (``{"choices": [...], "model": ..., "usage": ...}``).
        """
        client = self._get_client()
        model = kwargs.pop("model", None) or self.model
        system_text, converted = to_anthropic_messages(messages)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": kwargs.pop("max_tokens", 1024),
        }
        if system_text is not None:
            create_kwargs["system"] = system_text
        # Anthropic SDK 1.7.0 removed ``temperature`` from messages.create.
        # The role's ``temperature`` hint is dropped here — operators who
        # need per-request temperature should configure it on the
        # account / workspace level.
        create_kwargs.update({k: v for k, v in kwargs.items() if k != "temperature"})

        raw_headers = None
        try:
            raw_response = getattr(client.messages, "with_raw_response", None)
            if raw_response is not None:
                raw = raw_response.create(**create_kwargs)
                raw_headers = getattr(raw, "headers", None)
                response = raw.parse()
            else:
                response = client.messages.create(**create_kwargs)
        except Exception as exc:  # noqa: BLE001
            resp_obj = getattr(exc, "response", None)
            err_headers = getattr(resp_obj, "headers", None) if resp_obj is not None else None
            if err_headers:
                try:
                    from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

                    get_quota_tracker().record_headers(self.name, err_headers)
                except Exception:  # noqa: BLE001
                    pass
            raise

        if raw_headers:
            try:
                from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

                get_quota_tracker().record_headers(self.name, raw_headers)
            except Exception:  # noqa: BLE001
                pass

        out = self._normalize(response)
        raise_if_empty(
            out["choices"][0]["message"]["content"],
            getattr(response, "content", None),
            getattr(response, "stop_reason", None),
            self.name,
        )
        return out

    def check_quota(self) -> dict[str, Any]:
        return {
            "remaining": "unknown",
            "total": "unknown",
            "tier": "platform_api",
            "provider": self.provider_id,
            "model": self.model,
            "note": (
                "Anthropic API quota is visible in the Anthropic console at "
                "https://console.anthropic.com/settings/billing."
            ),
        }

    def list_models(self) -> list[str]:
        return [
            "claude-sonnet-5",
            "claude-opus-4-5",
            "claude-haiku-4-5-20251001",
        ]

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
    def _normalize(self, response: Any) -> dict[str, Any]:
        """Coerce an Anthropic Messages response into the OpenAI-style dict.

        Output shape::

            {
                "choices": [
                    {"message": {"content": "...", "role": "assistant"}}
                ],
                "model": "...",
                "usage": {"prompt_tokens": ..., "completion_tokens": ...},
            }
        """
        blocks = getattr(response, "content", None) or []
        content = extract_text(blocks)
        model = getattr(response, "model", None) or self.model
        usage = usage_from(getattr(response, "usage", None))

        return {
            "choices": [{"message": {"content": content, "role": "assistant"}}],
            "model": model,
            "usage": usage,
        }
