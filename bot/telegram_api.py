"""Async Telegram Bot API client.

A thin wrapper over ``httpx.AsyncClient`` covering the endpoints the bot
needs: sendMessage, editMessageText, sendChatAction. We deliberately
don't pull in the full ``python-telegram-bot`` runner — see plan for why.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx

from .models import BackendResult

API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramAPI:
    """Minimal async client. One shared ``httpx.AsyncClient`` per bot."""

    def __init__(self, token: str, *, timeout_s: float = 30.0) -> None:
        self._token = token
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, method: str, **params: Any) -> dict[str, Any]:
        url = API_BASE.format(token=self._token, method=method)
        # Drop keys whose value is None — Telegram rejects e.g.
        # ``parse_mode=null`` with HTTP 400.
        payload = {k: v for k, v in params.items() if v is not None}
        r = await self._client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method} failed: {data}")
        return data.get("result", {})

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_to: int | None = None,
    ) -> dict[str, Any]:
        return await self._post(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
            reply_to_message_id=reply_to,
        )

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        return await self._post(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
        )

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        """Throttled — Telegram requires 'typing' every ~5 s."""
        with contextlib.suppress(Exception):  # chat-action is best-effort
            await self._post("sendChatAction", chat_id=chat_id, action=action)

    @staticmethod
    def escape_markdown_v2(text: str) -> str:
        """Escape characters that MarkdownV2 treats specially.

        Only needed for messages sent with ``parse_mode="MarkdownV2"``; the
        bot's own replies are plain text and must not be escaped.
        """
        specials = r"_*[]()~`>#+-=|{}.!\\"
        out = []
        for ch in text:
            if ch in specials:
                out.append("\\" + ch)
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def format_result_block(result: BackendResult) -> str:
        """Render one backend's outcome as plain text.

        Reports are sent with ``parse_mode=None``: backend output is
        arbitrary (code, stack traces) and a single unbalanced ``*`` or
        ``_`` would make Telegram reject a MarkdownV2 message outright, so
        we deliberately do *not* escape or emit Markdown here.
        """
        check = "✅" if result.ok else "❌"
        seconds = result.latency_ms / 1000.0
        meta = f"{seconds:.1f}s"
        if result.tokens_in or result.tokens_out:
            meta += f" · in={result.tokens_in} out={result.tokens_out}"
        body = result.error if not result.ok and not result.text else result.text
        if len(body) > 1500:
            body = body[:1497] + "…"
        return f"{check} {result.backend} ({result.model or '?'}, {meta})\n{body}"


# Public helper used by tests and the polling loop
async def _idle_spinner(api: TelegramAPI, chat_id: int, stop: asyncio.Event) -> None:
    """Send 'typing' every 4.5s until ``stop`` is set."""
    while not stop.is_set():
        await api.send_chat_action(chat_id)
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.5)
        except TimeoutError:
            continue
