"""Shared base class for every OpenAI ``/chat/completions``-shaped pool.

GLM, MiniMax, OpenAI, OpenRouter, the Volcano sub-pools and the
``openai_compat`` long tail all speak the same wire format; this module
holds the one copy of the client / chat / normalise / health logic.

The leading underscore keeps :class:`core.registry.PluginRegistry` from
importing this module as a plugin. Concrete subclasses are still
auto-instantiated: ``_skip_auto_instantiate`` is *not* inherited (see
:meth:`OpenAIChatPlugin.__init_subclass__`).

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

from ._common import normalize_choices, raise_if_empty, resolve_timeout
from .base import ProviderPlugin

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - import guard
    OpenAI = None  # type: ignore[assignment,misc]


#: Model-name prefixes of OpenAI reasoning models. They reject a
#: non-default ``temperature`` and ``max_tokens`` (they want
#: ``max_completion_tokens``). ``gpt-5-chat*`` is a non-reasoning alias.
_REASONING_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")
_NON_REASONING_PREFIXES: tuple[str, ...] = ("gpt-5-chat",)


def is_reasoning_model(model: str | None) -> bool:
    """Return ``True`` for gpt-5 / o-series model names (``openai/`` prefix ok)."""
    if not model:
        return False
    name = model.lower().rsplit("/", 1)[-1]
    if name.startswith(_NON_REASONING_PREFIXES):
        return False
    return name.startswith(_REASONING_PREFIXES)


def _adapt_reasoning_kwargs(kwargs: dict[str, Any]) -> None:
    """Rewrite sampling params in-place for a reasoning model."""
    kwargs.pop("temperature", None)
    if "max_tokens" in kwargs and "max_completion_tokens" not in kwargs:
        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")


def _retry_kwargs_for_error(exc: Exception, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Return fixed kwargs if ``exc`` is a known parameter rejection, else ``None``.

    Handles both known 400s in a *single* retry: ``max_tokens`` →
    ``max_completion_tokens`` and an unsupported ``temperature``.
    """
    msg = str(exc)
    low = msg.lower()
    fixed = dict(kwargs)
    changed = False
    if "max_tokens" in msg and "max_completion_tokens" in msg and "max_tokens" in fixed:
        fixed["max_completion_tokens"] = fixed.pop("max_tokens")
        changed = True
    if "temperature" in low and "unsupported" in low and "temperature" in fixed:
        fixed.pop("temperature")
        changed = True
    if changed:
        # A model that rejects one of these almost always rejects the other.
        _adapt_reasoning_kwargs(fixed)
        return fixed
    return None


class OpenAIChatPlugin(ProviderPlugin):
    """Base for pools served through the OpenAI Python SDK.

    Subclasses set the class attributes below and call
    ``super().__init__`` with their pool name; everything else is shared.
    """

    provider_id = "openai_chat_base"

    #: Base itself must never be instantiated by the registry. Not inherited.
    _skip_auto_instantiate: bool = True

    #: Class-level defaults used when the constructor gets ``None``.
    DEFAULT_ENDPOINT: str = ""
    DEFAULT_MODEL: str | None = None
    ENV_KEYS: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Only an explicit declaration on the subclass itself counts —
        # otherwise every concrete plugin would inherit ``True`` from here
        # and silently disappear from registry discovery.
        cls._skip_auto_instantiate = bool(cls.__dict__.get("_skip_auto_instantiate", False))

    def __init__(
        self,
        name: str,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            endpoint=endpoint if endpoint is not None else self.DEFAULT_ENDPOINT,
            api_key=api_key if api_key is not None else self._key_from_env(),
            model=model if model is not None else self.DEFAULT_MODEL,
            capabilities=capabilities,
            metadata=metadata,
        )
        self._client: Any = None

    # ------------------------------------------------------------------
    @classmethod
    def _key_from_env(cls) -> str | None:
        for env in cls.ENV_KEYS:
            value = os.getenv(env)
            if value:
                return value
        return None

    def _timeout(self) -> float:
        return resolve_timeout(self.metadata)

    def _get_client(self) -> Any:
        if OpenAI is None:
            raise RuntimeError("openai package is not installed")
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key or "MISSING_API_KEY",
                base_url=self.default_endpoint,
                timeout=self._timeout(),
                # Retries belong to the failover layer (next pool), not the
                # SDK's own exponential backoff on the same failing pool.
                max_retries=0,
            )
        return self._client

    # ------------------------------------------------------------------
    # ProviderPlugin interface
    # ------------------------------------------------------------------
    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        client = self._get_client()
        model = kwargs.pop("model", None) or self.model
        if is_reasoning_model(model):
            _adapt_reasoning_kwargs(kwargs)
        raw_headers = None
        try:
            raw_response = getattr(client.chat.completions, "with_raw_response", None)
            if raw_response is not None:
                raw = raw_response.create(model=model, messages=messages, **kwargs)
                raw_headers = getattr(raw, "headers", None)
                response = raw.parse()
            else:
                response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        except Exception as exc:  # noqa: BLE001
            resp_obj = getattr(exc, "response", None)
            err_headers = getattr(resp_obj, "headers", None) if resp_obj is not None else None
            if err_headers:
                try:
                    from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

                    get_quota_tracker().record_headers(self.name, err_headers)
                except Exception:  # noqa: BLE001
                    pass
            fixed = _retry_kwargs_for_error(exc, kwargs)
            if fixed is None:
                raise
            raw_response = getattr(client.chat.completions, "with_raw_response", None)
            if raw_response is not None:
                raw = raw_response.create(model=model, messages=messages, **fixed)
                raw_headers = getattr(raw, "headers", None)
                response = raw.parse()
            else:
                response = client.chat.completions.create(model=model, messages=messages, **fixed)

        if raw_headers:
            try:
                from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

                get_quota_tracker().record_headers(self.name, raw_headers)
            except Exception:  # noqa: BLE001
                pass

        data = self._normalize(response)
        if raw_headers and isinstance(data, dict):
            data["_headers"] = dict(raw_headers)
        raise_if_empty(data, self.name)
        return data

    def check_quota(self) -> dict[str, Any]:
        try:
            from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

            snap = get_quota_tracker().get_snapshot(self.name)
            if snap:
                return {
                    "remaining": snap.remaining_tokens,
                    "remaining_requests": snap.remaining_requests,
                    "total": snap.limit_tokens,
                    "tier": self.metadata.get("tier"),
                    "provider": self.provider_id,
                    "model": self.model,
                    "is_exhausted": snap.is_exhausted(),
                    "seconds_until_reset": snap.seconds_until_reset(),
                }
        except Exception:  # noqa: BLE001
            pass
        return {
            "remaining": None,
            "total": None,
            "tier": self.metadata.get("tier"),
            "provider": self.provider_id,
            "model": self.model,
            "note": f"See the {self.name} console for quota.",
        }

    def list_models(self) -> list[str]:
        return [self.model or self.DEFAULT_MODEL or ""]

    def health_check(self) -> dict[str, Any]:
        return {
            "ok": bool(self.api_key) and bool(self.default_endpoint) and bool(self.model),
            "detail": (
                f"endpoint={self.default_endpoint} "
                f"model={self.model} key_present={bool(self.api_key)}"
            ),
        }

    # ------------------------------------------------------------------
    def _normalize(self, response: Any) -> dict[str, Any]:
        """Coerce an OpenAI SDK response object into a canonical plain dict."""
        if isinstance(response, dict):
            data = dict(response)
        else:
            try:
                data = response.model_dump()
            except AttributeError:
                data = dict(response)
        if not isinstance(data, dict):  # pragma: no cover - defensive
            data = {}
        return normalize_choices(data)


__all__ = ["OpenAIChatPlugin", "is_reasoning_model"]
