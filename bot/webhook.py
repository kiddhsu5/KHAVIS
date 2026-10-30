"""Webhook route — single FastAPI route + secret-token validation.

The route acknowledges Telegram immediately: the update is handed to a
:class:`~bot.background.BackgroundDispatcher` so a multi-minute fan-out
never holds the HTTP response open (Telegram would time out and redeliver
the same update). Redeliveries are dropped by ``update_id``.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from .models import IncomingMessage

if TYPE_CHECKING:
    from .background import BackgroundDispatcher, UpdateDeduper
    from .secrets import BotSecrets


async def webhook_route(
    request: Request,
    secrets: BotSecrets,
    handler: Callable[[IncomingMessage], Coroutine[Any, Any, None]],
    *,
    dispatcher: BackgroundDispatcher | None = None,
    deduper: UpdateDeduper | None = None,
) -> dict[str, bool]:
    """Validate ``X-Telegram-Bot-Api-Secret-Token`` and dispatch."""
    expected = (secrets.webhook_secret or "").encode()
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").encode()
    # An empty expected secret would make compare_digest(b"", b"") succeed
    # for a request with no header at all — never accept that.
    if not expected or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="bad secret token")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc!s}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid update")
    update_id = payload.get("update_id")
    if deduper is not None and isinstance(update_id, int) and deduper.seen(update_id):
        return {"ok": True}  # Telegram redelivery — already accepted
    msg = payload.get("message") or payload.get("edited_message") or {}
    chat = msg.get("chat") or {}
    text = msg.get("text") or ""
    if not chat or not text:
        # Telegram sends non-text updates too — we just drop them.
        return {"ok": True}
    incoming = IncomingMessage(
        update_id=int(payload.get("update_id", 0)),
        message_id=msg.get("message_id"),
        chat_id=int(chat.get("id", 0)),
        user_id=(msg.get("from") or {}).get("id"),
        text=text,
        is_command=text.startswith("/"),
    )
    if dispatcher is None:
        await handler(incoming)
    else:
        dispatcher.submit(handler(incoming), name=f"update-{incoming.update_id}")
    return {"ok": True}
