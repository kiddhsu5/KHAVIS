"""GLM (ZhipuAI / BigModel) provider plugin.

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

DEFAULT_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-5.3"
DEFAULT_MODELS = ["glm-5.3", "glm-5.3-flashx", "codegeex-4"]
ENV_KEY = "ZHIPUAI_API_KEY"


class GLMPlugin(OpenAIChatPlugin):
    """Zhipu GLM pool served through the OpenAI-compatible BigModel API."""

    provider_id = "glm"
    DEFAULT_ENDPOINT = DEFAULT_ENDPOINT
    DEFAULT_MODEL = DEFAULT_MODEL
    ENV_KEYS = (ENV_KEY,)

    def list_models(self) -> list[str]:
        return list(DEFAULT_MODELS)

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name="GLM-5.3",
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            capabilities=capabilities or ["中文", "英文", "推理", "工具調用", "程式碼"],
            metadata=metadata or {"region": "cn", "tier": "pro"},
        )

    def check_quota(self) -> dict[str, Any]:
        return {
            "remaining": None,
            "total": None,
            "tier": self.metadata.get("tier"),
            "provider": self.provider_id,
            "note": "BigModel quota endpoint requires authenticated account probe.",
        }
