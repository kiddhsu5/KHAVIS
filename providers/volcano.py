"""Volcano Ark (ByteDance) provider plugin.

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

DEFAULT_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3"
ENV_KEY = "BYTEDANCE_API_KEY"


# Each Volcano sub-pool is a fully-fledged ProviderPlugin instance.
# The factory below generates them dynamically so that the registry
# only needs to import :class:`VolcanoPluginFactory`.
SUB_POOLS: dict[str, dict[str, Any]] = {
    "DeepSeek-V4-Pro": {
        "model": "deepseek-v4-pro",
        "capabilities": ["中文", "英文", "推理", "程式碼", "工具調用"],
        "metadata": {"tier": "pro", "family": "deepseek"},
    },
    "DeepSeek-V4.1-Flash": {
        "model": "deepseek-v4.1-flash",
        "capabilities": ["中文", "英文", "速度優先", "推理"],
        "metadata": {"tier": "flash", "family": "deepseek"},
    },
    "GLM-5.3-Flash": {
        "model": "glm-5.3-flash",
        "capabilities": ["中文", "英文", "速度優先"],
        "metadata": {"tier": "flash", "family": "glm"},
    },
}


class VolcanoSubPool(OpenAIChatPlugin):
    """A single Volcano sub-pool bound to a specific model identifier.

    The ``_skip_auto_instantiate`` flag tells ``PluginRegistry`` not to
    call ``VolcanoSubPool()`` with no arguments — instances are produced
    by :class:`VolcanoPluginFactory` instead.
    """

    provider_id = "volcano"
    _skip_auto_instantiate: bool = True
    DEFAULT_ENDPOINT = DEFAULT_ENDPOINT
    ENV_KEYS = (ENV_KEY,)

    def __init__(
        self,
        pool_name: str,
        model: str,
        capabilities: list[str],
        metadata: dict[str, Any] | None = None,
        endpoint: str | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(
            name=pool_name,
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            capabilities=capabilities,
            metadata=metadata or {"tier": "standard"},
        )

    def check_quota(self) -> dict[str, Any]:
        return {
            "remaining": None,
            "total": None,
            "tier": self.metadata.get("tier"),
            "provider": self.provider_id,
            "model": self.model,
            "note": "Volcano Ark quota is visible in the ByteDance console.",
        }


class VolcanoPluginFactory:
    """Factory producing one :class:`VolcanoSubPool` per entry of :data:`SUB_POOLS`.

    The registry treats this class like a plugin; calling ``factory()``
    yields the list of sub-pool instances that should be registered.
    """

    name = "volcano"
    provider_id = "volcano"

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key

    def __call__(self) -> list[VolcanoSubPool]:
        pools: list[VolcanoSubPool] = []
        for pool_name, cfg in SUB_POOLS.items():
            pools.append(
                VolcanoSubPool(
                    pool_name=pool_name,
                    model=cfg["model"],
                    capabilities=cfg["capabilities"],
                    metadata=cfg.get("metadata", {}),
                    endpoint=self.endpoint,
                    api_key=self.api_key,
                )
            )
        return pools

    # ------------------------------------------------------------------
    # Aggregate helpers — useful for status pages.
    # ------------------------------------------------------------------
    def list_sub_pools(self) -> list[dict[str, Any]]:
        return [
            {"name": name, **{k: v for k, v in cfg.items() if k != "capabilities"}}
            for name, cfg in SUB_POOLS.items()
        ]


__all__ = ["VolcanoSubPool", "VolcanoPluginFactory", "VolcanoFactory", "SUB_POOLS"]


# Module-level factory instance the plugin registry can discover and call.
# ``VolcanoFactory()`` returns the list of three sub-pool plugins.
VolcanoFactory = VolcanoPluginFactory()
