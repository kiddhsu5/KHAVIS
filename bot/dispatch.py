"""Fan-out orchestrator: run N backends in parallel, collect results."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable

from .backends.base import Backend
from .models import BackendName, BackendResult, DispatchEnvelope, DispatchReport

_ALL_BACKENDS: tuple[BackendName, ...] = (
    "claude",
    "codex",
    "antigravity",
    "minimax",
    "opencode",
    "khavis",
)

# Kept as a module-level constant for backward compat with tests and any
# caller that imports ``DEFAULT_BACKEND_ORDER``. Prefer
# ``default_backend_order()`` — it honours the ``BOT_BACKENDS`` env var.
DEFAULT_BACKEND_ORDER: tuple[BackendName, ...] = _ALL_BACKENDS


def default_backend_order() -> tuple[BackendName, ...]:
    """Backends ``/run`` fans out to when no ``--only`` flag is given.

    Controlled by the ``BOT_BACKENDS`` env var (comma-separated list).
    Useful to temporarily disable a backend — e.g. ``claude`` before its
    OAuth login is done — without editing the code. Examples::

        BOT_BACKENDS=codex,K.H.A.V.I.S.   # skip claude
        BOT_BACKENDS=                    # (empty) = all three

    An unrecognised name is silently ignored. If *nothing* in the env
    var matches a known backend we fall back to all three rather than
    silently routing to zero backends.
    """
    raw = os.environ.get("BOT_BACKENDS", "").strip()
    if not raw:
        return _ALL_BACKENDS
    wanted = {p.strip() for p in raw.split(",") if p.strip()}
    filtered = tuple(b for b in _ALL_BACKENDS if b in wanted)
    return filtered or _ALL_BACKENDS


# Streaming callback: receives (newly_completed_result, all_results_so_far).
# The orchestrator awaits this after every backend finishes.
StreamingCallback = Callable[[BackendResult, list[BackendResult]], Awaitable[None]]


async def _safe_run(backend: Backend, envelope: DispatchEnvelope) -> BackendResult:
    """Run one backend, turning any escaped exception into a failed result.

    ``Backend.run`` is supposed to swallow its own errors, but a buggy
    backend must not take the whole fan-out down — and the failure has to
    carry the *real* backend name (``BackendResult.backend`` is a Literal,
    so a placeholder like ``"unknown"`` would itself raise ValidationError).
    """
    try:
        return await backend.run(envelope)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return BackendResult(
            backend=backend.name,  # type: ignore[arg-type]
            ok=False,
            error=f"dispatch error: {exc!r}",
        )


class DispatchRouter:
    """Owns the set of available backends; resolves ``envelope.only``."""

    def __init__(self, backends: dict[BackendName, Backend]) -> None:
        self._backends = backends

    @property
    def names(self) -> list[BackendName]:
        return list(self._backends.keys())  # type: ignore[return-value]

    def _resolve(self, envelope: DispatchEnvelope) -> list[Backend]:
        wanted = envelope.only
        if wanted is None:
            wanted = list(default_backend_order())
        return [self._backends[name] for name in wanted if name in self._backends]

    async def dispatch(self, envelope: DispatchEnvelope) -> DispatchReport:
        """Run the resolved backends in parallel; never raise."""
        targets = self._resolve(envelope)
        if not targets:
            return DispatchReport(envelope=envelope, results=[], consensus="")
        results = await asyncio.gather(*(_safe_run(b, envelope) for b in targets))
        consensus_text, source = synthesize(results)
        return DispatchReport(
            envelope=envelope,
            results=list(results),
            consensus=consensus_text,
            consensus_source=source,
        )

    async def dispatch_streaming(
        self,
        envelope: DispatchEnvelope,
        on_complete: StreamingCallback,
    ) -> DispatchReport:
        """Like ``dispatch`` but calls ``on_complete`` after each backend finishes.

        The callback receives the *new* BackendResult plus the full results
        list so far, so callers can re-render an in-place Telegram message
        edit. Telegram rate-limits edits to ~1/sec per chat, so the
        callback should debounce when streaming to many backends.
        """
        targets = self._resolve(envelope)
        if not targets:
            return DispatchReport(envelope=envelope, results=[], consensus="")

        results: list[BackendResult] = []
        tasks = [asyncio.create_task(_safe_run(b, envelope), name=b.name) for b in targets]
        for fut in asyncio.as_completed(tasks):
            result = await fut
            results.append(result)
            try:
                await on_complete(result, list(results))
            except Exception as exc:  # noqa: BLE001
                # Callback errors must not derail the whole dispatch.
                # The orchestrator will keep waiting for the remaining
                # backends; the next iteration's edit will catch up.
                print(f"[bot.dispatch] on_complete error: {exc!r}", flush=True)
        consensus_text, source = synthesize(results)
        return DispatchReport(
            envelope=envelope,
            results=results,
            consensus=consensus_text,
            consensus_source=source,
        )

    async def dispatch_async(
        self,
        envelope: DispatchEnvelope,
    ) -> AsyncIterator[BackendResult]:
        """Async iterator yielding each BackendResult as it finishes.

        Same as ``dispatch_streaming`` but without the callback — useful
        when the caller wants to drive the edits itself.
        """
        targets = self._resolve(envelope)
        if not targets:
            return
        tasks = [asyncio.create_task(_safe_run(b, envelope), name=b.name) for b in targets]
        for fut in asyncio.as_completed(tasks):
            yield await fut

    async def health_all(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for name, backend in self._backends.items():
            try:
                h = await backend.health()
                out[name] = {"ok": "yes" if h.ok else "no", "detail": h.detail}
            except Exception as exc:  # noqa: BLE001
                out[name] = {"ok": "no", "detail": repr(exc)}
        return out


def synthesize(
    results: Iterable[BackendResult],
) -> tuple[str, str]:
    """Pick the best non-empty backend answer.

    Heuristic: longest answer (in characters) above 20 chars wins.
    If none clear the threshold, return whatever's first.
    """
    items = [r for r in results if r.ok and r.text.strip()]
    if not items:
        return "", ""
    items.sort(key=lambda r: len(r.text), reverse=True)
    top = items[0]
    if len(top.text) >= 20:
        return top.text, top.backend
    return items[0].text, items[0].backend
