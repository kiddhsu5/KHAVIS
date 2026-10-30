"""Background dispatch helpers shared by the webhook route and polling loop.

Telegram retries a webhook delivery when the HTTP response does not come
back quickly, and a fan-out to three CLIs can take minutes. So both entry
points hand each update to :class:`BackgroundDispatcher` and return at once;
:class:`UpdateDeduper` drops the redeliveries that still slip through.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import OrderedDict
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger("bot.background")


class UpdateDeduper:
    """Short-lived memory of recently seen ``update_id`` values.

    Bounded both by age (``ttl_s``) and size (``max_entries``) so a flood of
    unique ids cannot grow memory without limit.
    """

    def __init__(self, ttl_s: float = 600.0, max_entries: int = 2048) -> None:
        self._ttl = ttl_s
        self._max = max_entries
        self._seen: OrderedDict[int, float] = OrderedDict()

    def seen(self, update_id: int) -> bool:
        """Return True if ``update_id`` was already accepted; record it otherwise."""
        now = time.monotonic()
        cutoff = now - self._ttl
        while self._seen:
            oldest_id, ts = next(iter(self._seen.items()))
            if ts >= cutoff and len(self._seen) < self._max:
                break
            self._seen.pop(oldest_id, None)
        if update_id in self._seen:
            return True
        self._seen[update_id] = now
        return False


class BackgroundDispatcher:
    """Run handler coroutines as tasks, capped by a concurrency semaphore.

    Strong references to in-flight tasks are kept in ``_tasks`` — the event
    loop only holds weak references, so an unreferenced task can be garbage
    collected mid-run.
    """

    def __init__(self, max_concurrency: int = 4) -> None:
        self._sem = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> int:
        return len(self._tasks)

    def submit(
        self, coro: Coroutine[Any, Any, Any], *, name: str | None = None
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(self._guarded(coro), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _guarded(self, coro: Coroutine[Any, Any, Any]) -> None:
        try:
            async with self._sem:
                await coro
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad update must not kill the bot
            log.exception("background handler failed")
        finally:
            # If we were cancelled while waiting on the semaphore the
            # coroutine never started; close it to avoid a "never awaited"
            # warning.
            coro.close()

    async def drain(self, timeout_s: float | None = None) -> None:
        """Wait for in-flight tasks (used by tests and graceful shutdown)."""
        if not self._tasks:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait(set(self._tasks), timeout=timeout_s)
        await asyncio.sleep(0)  # let done-callbacks prune ``_tasks``

    async def aclose(self) -> None:
        """Cancel whatever is still running and wait for it to unwind."""
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        # Done-callbacks run on the next loop tick; don't wait for them.
        self._tasks.difference_update(tasks)
