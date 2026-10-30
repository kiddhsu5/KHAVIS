"""Long-polling loop — fallback when webhook is unreachable.

Same message-routing path as the webhook route; the only difference is
the source. Both call ``handle_update()`` (in ``main.py``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import httpx

from .models import IncomingMessage

if TYPE_CHECKING:
    from .background import BackgroundDispatcher, UpdateDeduper
    from .secrets import BotSecrets

API_BASE = "https://api.telegram.org/bot{token}/getUpdates"


def _make_get_updates_url(token: str) -> str:
    return API_BASE.format(token=token)


async def long_poll_loop(
    secrets: BotSecrets,
    handler: Callable[[IncomingMessage], Coroutine[Any, Any, None]],
    *,
    stop: asyncio.Event,
    timeout_s: int = 30,
    poll_timeout_s: int = 30,
    dispatcher: BackgroundDispatcher | None = None,
    deduper: UpdateDeduper | None = None,
) -> None:
    """Run forever, calling ``handler`` for each new update.

    Robust to transient network failures (exponential back-off up to
    30 s). Stops cleanly when ``stop`` is set.

    With a ``dispatcher`` each update runs as a background task (bounded by
    the dispatcher's semaphore), so one slow fan-out does not stall the
    next ``getUpdates``; without one, updates are handled inline.

    ``deduper`` closes a gap the webhook route does not have: Telegram
    redelivers an update whose request it never got an answer for, and the
    webhook path filters those through :class:`UpdateDeduper` while polling
    did not. A redelivered dispatch means the whole fan-out runs twice —
    twice the model spend, and twice the messages in the chat.
    """
    url = _make_get_updates_url(secrets.bot_token)
    offset: int | None = None
    backoff = 1.0
    while not stop.is_set():
        params: dict[str, int | str] = {
            "timeout": poll_timeout_s,
            "allowed_updates": '["message"]',
        }
        if offset is not None:
            params["offset"] = offset
        # A fresh client per request + ``Connection: close`` so Telegram
        # never sees a reused connection from a previous long-poll that
        # was cut short. Otherwise the next ``getUpdates`` on the same
        # TCP socket can return ``409 Conflict: terminated by other
        # getUpdates request`` even though *we* are the only bot
        # instance — the previous request is still counted as pending
        # server-side.
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=float(poll_timeout_s) + 5.0,
                    write=10.0,
                    pool=10.0,
                ),
                headers={"Connection": "close"},
            ) as client:
                r = await client.get(url, params=params)
        except (TimeoutError, httpx.HTTPError) as exc:
            # Network glitch — back off and retry.
            wait = min(backoff, 30)
            await _sleep_or_stop(stop, wait)
            backoff = min(backoff * 2, 30)
            _log(f"getUpdates error: {exc!r}; sleeping {wait}s")
            continue
        if r.status_code != 200:
            wait = min(backoff, 30)
            await _sleep_or_stop(stop, wait)
            backoff = min(backoff * 2, 30)
            _log(f"getUpdates HTTP {r.status_code}; sleeping {wait}s")
            continue
        backoff = 1.0  # reset on success
        try:
            data = r.json()
        except ValueError:
            continue
        if not data.get("ok"):
            _log(f"getUpdates payload error: {data}")
            continue
        for upd in data.get("result") or []:
            offset = max(offset or 0, int(upd.get("update_id", 0)) + 1)
            msg = upd.get("message") or upd.get("edited_message") or {}
            text = msg.get("text") or ""
            chat = msg.get("chat") or {}
            if not chat or not text:
                continue
            incoming = IncomingMessage(
                update_id=int(upd.get("update_id", 0)),
                message_id=msg.get("message_id"),
                chat_id=int(chat.get("id", 0)),
                user_id=(msg.get("from") or {}).get("id"),
                text=text,
                is_command=text.startswith("/"),
            )
            if deduper is not None and deduper.seen(incoming.update_id):
                _log(f"dropping duplicate update {incoming.update_id}")
                continue
            if dispatcher is not None:
                dispatcher.submit(handler(incoming), name=f"update-{incoming.update_id}")
                continue
            try:
                await handler(incoming)
            except Exception as exc:  # noqa: BLE001
                _log(f"handler error: {exc!r}")


def _log(msg: str) -> None:
    """Best-effort logger (stdout). Tests replace via monkeypatch."""
    print(f"[bot.polling] {msg}", flush=True)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return
