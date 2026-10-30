"""Shared helpers for provider plugins (timeouts, response normalisation).

The leading underscore keeps :class:`core.registry.PluginRegistry` from
treating this module as a plugin.

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
from collections.abc import Mapping
from typing import Any

#: Default per-request timeout (seconds) for cloud providers.
DEFAULT_TIMEOUT_S = 60.0

#: Environment variable that overrides the default timeout for every pool.
TIMEOUT_ENV = "KHAVIS_PROVIDER_TIMEOUT_S"


def _as_positive_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def resolve_timeout(
    metadata: Mapping[str, Any] | None = None,
    override: Any = None,
    default: float = DEFAULT_TIMEOUT_S,
) -> float:
    """Resolve the request timeout in seconds.

    Precedence: explicit ``override`` (e.g. a ``timeout=`` chat kwarg) →
    ``metadata["timeout_s"]`` / ``metadata["timeout"]`` → the
    ``KHAVIS_PROVIDER_TIMEOUT_S`` environment variable → ``default``.
    Invalid / non-positive values are ignored.
    """
    candidates: list[Any] = [override]
    if metadata:
        candidates += [metadata.get("timeout_s"), metadata.get("timeout")]
    candidates.append(os.getenv(TIMEOUT_ENV))
    for value in candidates:
        parsed = _as_positive_float(value)
        if parsed is not None:
            return parsed
    return float(default)


def text_of(content: Any) -> str:
    """Flatten an OpenAI-style ``content`` value to plain text.

    ``None`` → ``""``; a list of content parts → the ``text`` of every text
    part joined with newlines (non-text parts such as images are dropped);
    anything else → ``str(content)``.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, Mapping):
                if part.get("type", "text") == "text" and part.get("text") is not None:
                    parts.append(str(part["text"]))
            elif getattr(part, "text", None) is not None:
                parts.append(str(part.text))
        return "\n".join(p for p in parts if p)
    return str(content)


def _message_has_tool_calls(message: Mapping[str, Any]) -> bool:
    return bool(message.get("tool_calls") or message.get("function_call"))


def normalize_choices(data: dict[str, Any]) -> dict[str, Any]:
    """Guarantee ``data["choices"]`` is a list and every content is a ``str``.

    Upstreams return ``content: null`` for tool-call turns (and some for
    refusals); downstream consumers call ``str(content)`` and would surface
    the literal ``"None"``.
    """
    choices = data.get("choices")
    if not isinstance(choices, list):
        data["choices"] = []
        return data
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            message["content"] = text_of(message.get("content"))
    return data


def raise_if_empty(data: Mapping[str, Any], provider: str) -> None:
    """Raise ``RuntimeError`` when the upstream answered with nothing usable.

    Only fires when ``choices`` is non-empty but every choice has empty
    content and no tool/function call — an HTTP 200 with a blank answer
    (content filter, reasoning ate the whole token budget, …). Raising
    lets the failover layer try the next pool instead of returning an
    empty string to the user. A response without any ``choices`` is left
    alone (it is normalised to ``choices: []`` — existing contract).
    """
    choices = data.get("choices") or []
    if not choices:
        return
    for choice in choices:
        if not isinstance(choice, Mapping):
            return
        message = choice.get("message")
        if not isinstance(message, Mapping):
            # Legacy completion shape ({"text": ...}) or unknown — do not judge.
            if choice.get("text"):
                return
            continue
        if text_of(message.get("content")).strip():
            return
        if _message_has_tool_calls(message) or message.get("refusal"):
            return
    raise RuntimeError(f"{provider}: empty response (no content and no tool_calls)")


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "TIMEOUT_ENV",
    "normalize_choices",
    "raise_if_empty",
    "resolve_timeout",
    "text_of",
]
