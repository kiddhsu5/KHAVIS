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

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        requested = kwargs.get("model") or self.model or self.DEFAULT_MODEL
        try:
            return super().chat(messages, **kwargs)
        except Exception as exc:  # noqa: BLE001
            err_s = str(exc)
            # Coding Plan subscriptions list glm-5.3-flashx in /models yet reject it
            # with code 1311 (or 1211 on retired aliases); retry once on glm-5.3.
            if requested != "glm-5.3" and ("1311" in err_s or "1211" in err_s):
                retry_kw = dict(kwargs)
                retry_kw["model"] = "glm-5.3"
                return super().chat(messages, **retry_kw)
            raise
