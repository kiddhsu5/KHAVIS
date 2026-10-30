"""OpenAI Platform API provider plugin.

gpt-5 / o-series reasoning models reject a non-default ``temperature`` and
``max_tokens``; :class:`~providers._openai_base.OpenAIChatPlugin` drops /
renames those up front by model-name prefix and, for anything the prefix
check misses, retries once with both fixes applied.

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

DEFAULT_ENDPOINT = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5-mini"
ENV_KEY = "OPENAI_API_KEY"


class OpenAIPlugin(OpenAIChatPlugin):
    """OpenAI Platform API pool (gpt-5 series)."""

    provider_id = "openai"
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
            name="OpenAI-API",
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            capabilities=capabilities
            or [
                "英文",
                "程式碼",
                "推理",
                "工具調用",
                "速度優先",
            ],
            metadata=metadata or {"region": "us", "tier": "platform"},
        )

    def check_quota(self) -> dict[str, Any]:
        return {
            "remaining": "unknown",
            "total": "unknown",
            "tier": "platform_api",
            "provider": self.provider_id,
            "model": self.model,
            "note": (
                "OpenAI Platform API quota is visible in the OpenAI dashboard "
                "at https://platform.openai.com/usage."
            ),
        }

    def list_models(self) -> list[str]:
        return [
            "gpt-5",
            "gpt-5-mini",
            "gpt-5-codex",
            "gpt-5-nano",
        ]
