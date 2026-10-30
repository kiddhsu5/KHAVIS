"""NVIDIA Cloud (NIM) provider plugin.

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

from ._common import normalize_choices, raise_if_empty, resolve_timeout
from .base import ProviderPlugin

DEFAULT_ENDPOINT = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "meta/llama-3.3-70b-instruct"
ENV_KEY = "NVIDIA_API_KEY"


class NvidiaPlugin(ProviderPlugin):
    """NVIDIA NIM endpoint pool.

    NIM exposes an OpenAI-compatible ``/chat/completions`` route, but the
    model catalogue is dynamic, so we keep ``list_models`` as a live
    best-effort lookup that gracefully degrades if the catalogue endpoint
    is not reachable (env var missing, offline, ...).
    """

    provider_id = "nvidia"

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name="NVIDIA-Cloud",
            endpoint=endpoint if endpoint is not None else DEFAULT_ENDPOINT,
            api_key=api_key if api_key is not None else os.getenv(ENV_KEY),
            model=model if model is not None else DEFAULT_MODEL,
            capabilities=capabilities
            or [
                "英文",
                "推理",
                "工具調用",
                "程式碼",
                "Embedding",
            ],
            metadata=metadata or {"region": "us", "tier": "nim"},
        )

    # ------------------------------------------------------------------
    # ProviderPlugin interface
    # ------------------------------------------------------------------
    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("NVIDIA_API_KEY not configured")
        model = kwargs.pop("model", None) or self.model
        # Transport option, not a request-body field.
        timeout = resolve_timeout(self.metadata, kwargs.pop("timeout", None))
        url = f"{self.default_endpoint.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"model": model, "messages": messages}
        payload.update(kwargs)
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if hasattr(resp, "headers") and resp.headers:
            try:
                from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

                get_quota_tracker().record_headers(self.name, resp.headers)
            except Exception:  # noqa: BLE001
                pass
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        normalize_choices(data)
        raise_if_empty(data, self.name)
        return data

    def check_quota(self) -> dict[str, Any]:
        return {
            "remaining": None,
            "total": None,
            "tier": self.metadata.get("tier"),
            "provider": self.provider_id,
            "note": "NVIDIA NIM quota is surfaced via the NVIDIA developer dashboard.",
        }

    def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        url = f"{self.default_endpoint.rstrip('/')}/models"
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                return [m.get("id", "") for m in data["data"] if m.get("id")]
            if isinstance(data, list):
                return [m.get("id", "") for m in data if isinstance(m, dict)]
        except Exception:
            return []
        return []

    def health_check(self) -> dict[str, Any]:
        return {
            "ok": bool(self.api_key) and bool(self.default_endpoint) and bool(self.model),
            "detail": (
                f"endpoint={self.default_endpoint} "
                f"model={self.model} key_present={bool(self.api_key)}"
            ),
        }
