"""OpenRouter provider plugin.

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

from typing import Any

from ._openai_base import OpenAIChatPlugin

DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "meta-llama/llama-3.1-8b-instruct:free"
ENV_KEY = "OPENROUTER_API_KEY"


class OpenRouterPlugin(OpenAIChatPlugin):
    """OpenRouter free-tier pool (auto-routed upstream)."""

    provider_id = "openrouter"
    DEFAULT_ENDPOINT = DEFAULT_ENDPOINT
    DEFAULT_MODEL = DEFAULT_MODEL
    ENV_KEYS = (ENV_KEY,)

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name="OpenRouter-Free",
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            capabilities=capabilities
            or [
                "英文",
                "速度優先",
                "工具調用",
            ],
            metadata=metadata or {"region": "global", "tier": "free"},
        )

    def check_quota(self) -> dict[str, Any]:
        try:
            from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

            api_res = get_quota_tracker().query_pool_quota(self)
            if api_res:
                return {
                    "remaining": None,
                    "total": None,
                    "balance_usd": api_res.get("balance_usd"),
                    "usage_usd": api_res.get("usage_usd"),
                    "is_free_tier": api_res.get("is_free_tier"),
                    "tier": "free" if api_res.get("is_free_tier") else self.metadata.get("tier"),
                    "provider": self.provider_id,
                }
        except Exception:  # noqa: BLE001
            pass
        return {
            "remaining": None,
            "total": None,
            "tier": self.metadata.get("tier"),
            "provider": self.provider_id,
            "note": "OpenRouter free-tier limits are dynamic per model.",
        }
