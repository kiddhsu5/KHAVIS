"""Google Gemini provider plugin.

Uses the **current** Google Gen AI SDK (``google-genai``), not the
retired ``google-generativeai`` package — that library is EOL and its
grpc import caused native crashes in the test suite.

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

from ._common import resolve_timeout, text_of
from .base import ProviderPlugin

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:  # pragma: no cover - optional dep
    genai = None  # type: ignore[assignment,misc]
    genai_types = None  # type: ignore[assignment,misc]


# Use the ``-latest`` aliases, not pinned 2.x names: Google revoked
# ``gemini-2.5-flash`` / ``gemini-2.5-pro`` for new users (404
# ``This model ... is no longer available``), which silently broke this
# pool. The aliases track the current generation so we do not repeat
# that breakage every time Google retires a version.
DEFAULT_MODELS: list[str] = [
    "gemini-4-argon",
    "gemini-3.8-flash",
    "gemini-flash-latest",
    "gemini-pro-latest",
]
DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com"
ENV_KEY = "GOOGLE_API_KEY"

#: Appended when the history ends on a model turn: Gemini expects the
#: conversation to end with the user, and an assistant-final history
#: means "carry on from here".
CONTINUE_PROMPT = "Continue."


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def to_gemini_contents(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert OpenAI-style messages to ``(system_instruction, contents)``.

    * ``system`` messages → one ``system_instruction`` string.
    * ``assistant`` / ``model`` → role ``model``; everything else → ``user``.
      ``tool`` results are kept as user text prefixed ``[tool result ...]``
      (native ``function_response`` parts would need call ids we do not track).
    * Empty turns are dropped and consecutive same-role turns are merged
      into one content with several parts, keeping roles alternating.
    * If the last turn is from the model, a short user ``Continue.`` turn
      is appended so the request is well-formed.
    """
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        text = text_of(m.get("content"))
        if role == "system":
            if text.strip():
                system_parts.append(text)
            continue
        if role in ("tool", "function"):
            ref = m.get("tool_call_id") or m.get("name") or ""
            header = f"[tool result {ref}]" if ref else "[tool result]"
            text = f"{header}\n{text}" if text else header
        g_role = "model" if role in ("assistant", "model") else "user"
        if not text.strip():
            continue
        if contents and contents[-1]["role"] == g_role:
            contents[-1]["parts"].append({"text": text})
        else:
            contents.append({"role": g_role, "parts": [{"text": text}]})
    if contents and contents[-1]["role"] == "model":
        contents.append({"role": "user", "parts": [{"text": CONTINUE_PROMPT}]})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, contents


class GeminiPlugin(ProviderPlugin):
    """Google Gemini pool (``google-genai`` SDK).

    Supports multiple model variants (``gemini-flash-latest``,
    ``gemini-pro-latest``). The first entry of ``models`` is treated as
    the default unless overridden at construction time.
    """

    provider_id = "gemini"

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        models: list[str] | None = None,
    ) -> None:
        self._models: list[str] = list(models) if models else list(DEFAULT_MODELS)
        super().__init__(
            name="Google-Gemini",
            endpoint=endpoint if endpoint is not None else DEFAULT_ENDPOINT,
            api_key=api_key if api_key is not None else os.getenv(ENV_KEY),
            model=model if model is not None else self._models[0],
            capabilities=capabilities
            or [
                "英文",
                "推理",
                "工具調用",
                "速度優先",
                "Embedding",
            ],
            metadata=metadata or {"region": "global", "tier": "pro"},
        )
        self._client: Any = None

    # ------------------------------------------------------------------
    def _http_options(self, timeout_s: float | None = None) -> Any:
        opts: dict[str, Any] = {
            # google-genai expects milliseconds.
            "timeout": int((timeout_s or resolve_timeout(self.metadata)) * 1000),
        }
        endpoint = (self.default_endpoint or "").rstrip("/")
        if endpoint and endpoint != DEFAULT_ENDPOINT:
            opts["base_url"] = endpoint
        return genai_types.HttpOptions(**opts)

    def _get_client(self) -> Any:
        if genai is None:
            raise RuntimeError("google-genai package is not installed (pip install google-genai)")
        if not self.api_key:
            raise RuntimeError("GOOGLE_API_KEY not configured")
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key, http_options=self._http_options())
        return self._client

    # ------------------------------------------------------------------
    # ProviderPlugin interface
    # ------------------------------------------------------------------
    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        client = self._get_client()
        target_model = kwargs.pop("model", None) or self.model
        max_tokens = kwargs.pop("max_tokens", None) or kwargs.pop("max_completion_tokens", None)
        temperature = kwargs.pop("temperature", None)
        timeout = kwargs.pop("timeout", None)

        system_text, contents = to_gemini_contents(messages)
        if not contents:
            raise RuntimeError("gemini: no user/assistant messages to send")

        config_kwargs: dict[str, Any] = {}
        if system_text:
            config_kwargs["system_instruction"] = system_text
        if max_tokens:
            config_kwargs["max_output_tokens"] = int(max_tokens)
        if temperature is not None:
            config_kwargs["temperature"] = float(temperature)
        if timeout is not None:
            config_kwargs["http_options"] = self._http_options(float(timeout))

        response = client.models.generate_content(
            model=target_model,
            contents=contents,
            config=genai_types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
        )
        text = getattr(response, "text", None)
        text = text if isinstance(text, str) else ""
        if not text.strip():
            # Safety block, MAX_TOKENS spent on thinking, … — let failover retry.
            raise RuntimeError(f"{self.name}: empty response")

        usage: dict[str, Any] = {}
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            prompt = _int(getattr(meta, "prompt_token_count", 0))
            # Thinking tokens are billed as output but reported separately.
            completion = _int(getattr(meta, "candidates_token_count", 0)) + _int(
                getattr(meta, "thoughts_token_count", 0)
            )
            total = _int(getattr(meta, "total_token_count", 0)) or prompt + completion
            usage = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
            }

        return {
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "model": target_model,
            "usage": usage,
        }

    def check_quota(self) -> dict[str, Any]:
        return {
            "remaining": None,
            "total": None,
            "tier": self.metadata.get("tier"),
            "provider": self.provider_id,
            "note": "Gemini quota is visible via the Google AI Studio console.",
        }

    def list_models(self) -> list[str]:
        return list(self._models)

    def health_check(self) -> dict[str, Any]:
        return {
            "ok": bool(self.api_key) and bool(self.model),
            "detail": (
                f"endpoint={self.default_endpoint} "
                f"models={self._models} key_present={bool(self.api_key)} sdk=google-genai"
            ),
        }
