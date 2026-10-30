"""OpenAI-style ↔ Anthropic Messages conversion shared by ``claude`` and ``mimo``.

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

import json
from collections.abc import Iterable, Mapping
from typing import Any

from ._common import text_of


def _describe_tool_calls(tool_calls: Any) -> str:
    """Render OpenAI ``tool_calls`` as readable text (best effort)."""
    lines: list[str] = []
    for call in tool_calls or []:
        if not isinstance(call, Mapping):
            continue
        fn = call.get("function") or {}
        name = fn.get("name") if isinstance(fn, Mapping) else None
        args = fn.get("arguments") if isinstance(fn, Mapping) else None
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False) if args is not None else ""
        call_id = call.get("id")
        prefix = f"[tool_call {call_id}" if call_id else "[tool_call"
        lines.append(f"{prefix} {name or '?'}({args})]")
    return "\n".join(lines)


def to_anthropic_messages(
    messages: Iterable[Mapping[str, Any]],
) -> tuple[str | None, list[dict[str, str]]]:
    """Split OpenAI-style messages into Anthropic ``(system, messages)``.

    * ``content`` may be ``None``, a string, or a list of content parts;
      it is flattened to text (non-text parts such as images are dropped).
    * Every ``system`` message (wherever it appears) is concatenated into
      the single Anthropic ``system`` string.
    * An assistant turn with no text is skipped, unless it carries
      ``tool_calls`` — those are kept as a textual description.
    * ``tool`` / ``function`` results are *not* sent as native
      ``tool_result`` blocks (that would require matching ``tool_use``
      ids the router does not track); they are preserved as a ``user``
      text turn prefixed with ``[tool result ...]``.
    * Unknown roles are treated as user input so no data is dropped.
    * Empty turns are dropped (Anthropic rejects empty text content) and
      consecutive same-role turns are merged so roles alternate.
    """
    system_parts: list[str] = []
    converted: list[dict[str, str]] = []

    def _append(role: str, text: str) -> None:
        if not text.strip():
            return
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"] = f"{converted[-1]['content']}\n\n{text}"
        else:
            converted.append({"role": role, "content": text})

    for msg in messages:
        role = msg.get("role")
        text = text_of(msg.get("content"))
        if role == "system":
            if text.strip():
                system_parts.append(text)
        elif role == "assistant":
            calls = _describe_tool_calls(msg.get("tool_calls"))
            _append("assistant", "\n".join(p for p in (text, calls) if p))
        elif role in ("tool", "function"):
            ref = msg.get("tool_call_id") or msg.get("name") or ""
            header = f"[tool result {ref}]" if ref else "[tool result]"
            _append("user", f"{header}\n{text}" if text else header)
        else:
            # "user" and any unknown role.
            _append("user", text)

    system_text = "\n\n".join(system_parts) if system_parts else None
    return system_text, converted


def _block_get(block: Any, key: str) -> Any:
    if isinstance(block, Mapping):
        return block.get(key)
    return getattr(block, key, None)


def extract_text(blocks: Iterable[Any] | None) -> str:
    """Join the ``text`` of every text content block.

    ``thinking`` / ``tool_use`` blocks (which have no ``text``) are ignored.
    Dict blocks without an explicit ``type`` are treated as text.
    """
    parts: list[str] = []
    for block in blocks or []:
        if isinstance(block, Mapping):
            if block.get("type", "text") == "text" and block.get("text") is not None:
                parts.append(str(block["text"]))
        else:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(str(text))
    return "".join(parts)


def has_tool_use(blocks: Iterable[Any] | None, stop_reason: Any = None) -> bool:
    if stop_reason == "tool_use":
        return True
    return any(_block_get(b, "type") == "tool_use" for b in blocks or [])


def usage_from(raw: Any) -> dict[str, int]:
    """Map Anthropic ``usage`` (object or dict) to OpenAI-style counters."""
    if raw is None:
        return {}

    def _num(key: str) -> int:
        value = _block_get(raw, key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    prompt = _num("input_tokens")
    completion = _num("output_tokens")
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def raise_if_empty(
    text: str, blocks: Iterable[Any] | None, stop_reason: Any, provider: str
) -> None:
    """Raise when the reply has no text and no tool call, so failover kicks in."""
    if text.strip() or has_tool_use(blocks, stop_reason):
        return
    raise RuntimeError(f"{provider}: empty response (stop_reason={stop_reason!r})")


__all__ = [
    "extract_text",
    "has_tool_use",
    "raise_if_empty",
    "to_anthropic_messages",
    "usage_from",
]
