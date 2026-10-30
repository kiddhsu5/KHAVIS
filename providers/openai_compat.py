"""OpenAI-compatible multi-provider factory (Groq, Mistral, xAI, …).

One factory produces one pool per entry of :data:`SUB_POOLS`. Every
upstream speaks the OpenAI ``/v1/chat/completions`` shape, so a single
plugin class covers the long tail of popular BYOK vendors without a
file per provider.

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

from ._openai_base import OpenAIChatPlugin

# name → default endpoint / model / env key / capabilities / metadata
SUB_POOLS: dict[str, dict[str, Any]] = {
    "Groq-Llama": {
        "endpoint": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "env_key": "GROQ_API_KEY",
        "capabilities": ["英文", "程式碼", "推理", "速度優先", "工具調用"],
        "metadata": {"region": "us", "tier": "free_tier"},
    },
    "Mistral-Large": {
        "endpoint": "https://api.mistral.ai/v1",
        "model": "mistral-large-latest",
        "env_key": "MISTRAL_API_KEY",
        "capabilities": ["英文", "程式碼", "推理", "工具調用", "長文"],
        "metadata": {"region": "eu", "tier": "platform_api"},
    },
    "Cohere-Command": {
        "endpoint": "https://api.cohere.ai/compatibility/v1",
        "model": "command-r-plus",
        "env_key": "COHERE_API_KEY",
        "capabilities": ["英文", "推理", "工具調用", "長文"],
        "metadata": {"region": "us", "tier": "platform_api"},
    },
    "xAI-Grok": {
        "endpoint": "https://api.x.ai/v1",
        "model": "grok-3",
        "env_key": "XAI_API_KEY",
        "capabilities": ["英文", "程式碼", "推理", "長文"],
        "metadata": {"region": "us", "tier": "platform_api"},
    },
    "Together-Llama": {
        "endpoint": "https://api.together.xyz/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "env_key": "TOGETHER_API_KEY",
        "capabilities": ["英文", "程式碼", "推理"],
        "metadata": {"region": "us", "tier": "platform_api"},
    },
    "Fireworks-Llama": {
        "endpoint": "https://api.fireworks.ai/inference/v1",
        "model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "env_key": "FIREWORKS_API_KEY",
        "capabilities": ["英文", "程式碼", "速度優先"],
        "metadata": {"region": "us", "tier": "platform_api"},
    },
    "Perplexity-Sonar": {
        "endpoint": "https://api.perplexity.ai",
        "model": "sonar-pro",
        "env_key": "PERPLEXITY_API_KEY",
        "capabilities": ["英文", "推理", "長文"],
        "metadata": {"region": "us", "tier": "platform_api"},
    },
    "Moonshot-Kimi": {
        "endpoint": "https://api.moonshot.cn/v1",
        "model": "kimi-k2-0905-preview",
        "env_key": "MOONSHOT_API_KEY",
        "capabilities": ["中文", "英文", "程式碼", "推理", "長文"],
        "metadata": {"region": "cn", "tier": "platform_api"},
    },
    "Qwen-DashScope": {
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
        "env_key": "DASHSCOPE_API_KEY",
        "capabilities": ["中文", "英文", "程式碼", "推理", "工具調用"],
        "metadata": {"region": "cn", "tier": "platform_api"},
    },
    "DeepSeek-Official": {
        "endpoint": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
        "capabilities": ["中文", "英文", "程式碼", "推理"],
        "metadata": {"region": "cn", "tier": "platform_api"},
    },
    "HuggingFace-Inference": {
        "endpoint": "https://router.huggingface.co/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "env_key": "HF_API_KEY",
        "capabilities": ["英文", "程式碼"],
        "metadata": {"region": "us", "tier": "platform_api"},
    },
}


class OpenAICompatSubPool(OpenAIChatPlugin):
    """A single OpenAI-compatible upstream (one pool)."""

    provider_id = "openai_compat"
    _skip_auto_instantiate = True

    def __init__(
        self,
        pool_name: str,
        endpoint: str,
        model: str,
        env_key: str,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        api_key: str | None = None,
    ) -> None:
        self.env_key = env_key
        super().__init__(
            name=pool_name,
            endpoint=endpoint,
            api_key=api_key if api_key is not None else os.getenv(env_key),
            model=model,
            capabilities=capabilities or ["英文", "程式碼"],
            metadata=metadata or {},
        )


class OpenAICompatFactory:
    """Registry-discoverable factory: one call ⇒ every SUB_POOLS instance."""

    name = "openai_compat"
    provider_id = "openai_compat"

    def __init__(self, endpoint: str | None = None, api_key: str | None = None) -> None:
        self.endpoint = endpoint
        self.api_key = api_key

    def __call__(self) -> list[OpenAICompatSubPool]:
        pools: list[OpenAICompatSubPool] = []
        for pool_name, cfg in SUB_POOLS.items():
            endpoint = self.endpoint if self.endpoint is not None else cfg["endpoint"]
            key = self.api_key
            if key is None:
                key = os.getenv(cfg["env_key"])
            pools.append(
                OpenAICompatSubPool(
                    pool_name=pool_name,
                    endpoint=endpoint,
                    model=cfg["model"],
                    env_key=cfg["env_key"],
                    capabilities=cfg.get("capabilities"),
                    metadata=cfg.get("metadata"),
                    api_key=key,
                )
            )
        return pools

    def list_sub_pools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "endpoint": cfg["endpoint"],
                "model": cfg["model"],
                "env_key": cfg["env_key"],
            }
            for name, cfg in SUB_POOLS.items()
        ]


# Module-level instance the registry discovers (callable + provider_id).
OpenAICompatPlugin = OpenAICompatFactory()

__all__ = [
    "SUB_POOLS",
    "OpenAICompatSubPool",
    "OpenAICompatFactory",
    "OpenAICompatPlugin",
]
