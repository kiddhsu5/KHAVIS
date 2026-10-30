"""MiniMax-M3 provider plugin.

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

DEFAULT_ENDPOINT = "https://api.MiniMax.chat/v1"
DEFAULT_MODEL = "MiniMax-M3.1-Flash"
DEFAULT_MODELS = ["MiniMax-M3.1-Flash", "MiniMax-M3"]

# Historical env var name. Not PEP-8 uppercase, but it is a public contract:
# it is documented in .env.example. Renaming it would silently break every
# existing deployment, so ruff's SIM112 is suppressed rather than obeyed.
ENV_KEY = "MiniMax_API_KEY"  # noqa: SIM112

# Deprecation shim: the conventional upper-case name is what
# config/pools.yaml (``env_key: "MINIMAX_API_KEY"``) and the token-plan docs
# use. Both are accepted; the historical name wins when both are set.
ENV_KEY_UPPER = "MINIMAX_API_KEY"


class MiniMaxPlugin(OpenAIChatPlugin):
    """MiniMax-M3 pool served through an OpenAI-compatible HTTP API."""

    provider_id = "MiniMax"
    DEFAULT_ENDPOINT = DEFAULT_ENDPOINT
    DEFAULT_MODEL = DEFAULT_MODEL
    ENV_KEYS = (ENV_KEY, ENV_KEY_UPPER)

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name="MiniMax-M3",
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            capabilities=capabilities or ["中文", "英文", "推理", "工具調用"],
            metadata=metadata or {"region": "global", "tier": "standard"},
        )

    def check_quota(self) -> dict[str, Any]:
        return {
            "remaining": None,
            "total": None,
            "tier": self.metadata.get("tier"),
            "provider": self.provider_id,
            "note": "Quota endpoint not exposed by MiniMax public API.",
        }
