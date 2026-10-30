"""FastAPI app: webhook + lifespan + ``khavis-bot`` entry point.

Two ways to run:

1. ``uvicorn bot.main:app --host 0.0.0.0 --port 8080``  — webhook mode
2. ``python -m bot.main --mode polling``                — long-poll mode

Both modes start the same handler pipeline (handlers → dispatch → aggregator
→ Telegram sendMessage).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .aggregator import format_report
from .backends import (
    AntigravityBackend,
    ClaudeBackend,
    CodexBackend,
    JudgeBackend,
    KhavisBackend,
    MinimaxBackend,
    OpencodeBackend,
)
from .background import BackgroundDispatcher, UpdateDeduper
from .dispatch import DispatchRouter
from .handlers import HandlerDeps, is_allowed, route_command
from .models import BackendName, BackendResult, DispatchReport, IncomingMessage
from .pairing_loader import load_pairing
from .polling import long_poll_loop
from .secrets import BotSecrets
from .telegram_api import TelegramAPI


# ---------------------------------------------------------------------------
# Build the dispatch + telegram service. Kept module-level so uvicorn's
# import path ``bot.main:app`` works without extra ceremony.
# ---------------------------------------------------------------------------
def _build_router_and_deps(secrets: BotSecrets) -> tuple[DispatchRouter, HandlerDeps]:
    """Wire concrete backends + handler deps."""
    backends: dict[BackendName, Any] = {
        "claude": ClaudeBackend(timeout_s=secrets.backend_timeout_s),
        "codex": CodexBackend(timeout_s=secrets.backend_timeout_s),
        "antigravity": AntigravityBackend(timeout_s=secrets.backend_timeout_s),
        "minimax": MinimaxBackend(timeout_s=secrets.backend_timeout_s),
        "opencode": OpencodeBackend(timeout_s=secrets.backend_timeout_s),
        "khavis": KhavisBackend(),
    }
    router = DispatchRouter(backends)

    telegram = TelegramAPI(secrets.bot_token)
    pairing = load_pairing()
    allowed = sorted(set(secrets.allowed_chat_ids) | set(pairing.allow_from))

    deps = HandlerDeps(
        telegram=telegram,
        allowlist=allowed,
        backends={k: v for k, v in backends.items()},  # type: ignore[dict-item]
        pools=[],  # populated lazily via ``_populate_pools()`` in lifespan
        # Stored on deps so the /run handler can pull the judge without
        # re-running the lifespan.
        judge=JudgeBackend(),
        secrets=secrets,
    )
    return router, deps


async def _populate_pools(deps: HandlerDeps) -> None:
    """Best-effort: load K.H.A.V.I.S. pools so ``/pools`` can answer."""
    try:
        from core.registry import PluginRegistry  # noqa: PLC0415

        reg = PluginRegistry().discover()
        pools_yaml = reg.project_root / "config" / "pools.yaml"
        if pools_yaml.exists():
            reg.apply_pools_config(pools_yaml)
        deps.pools = [
            (p.name, list(p.capabilities))
            for p in reg.all()  # type: ignore[attr-defined]
        ]
    except Exception:  # noqa: BLE001
        deps.pools = []


def _max_concurrency() -> int:
    """Upper bound on updates handled at once (``BOT_MAX_CONCURRENCY``, default 4)."""
    try:
        return max(1, int(os.environ.get("BOT_MAX_CONCURRENCY", "4")))
    except ValueError:
        return 4


def _make_app(secrets: BotSecrets) -> FastAPI:
    """Build the FastAPI app bound to the given secrets."""
    router, deps = _build_router_and_deps(secrets)
    state: dict[str, Any] = {"router": router, "deps": deps, "polling_stop": None}
    handler = _build_handler(router, deps)
    deduper = UpdateDeduper()
    # Created lazily inside the running loop (asyncio primitives bind to it).
    holder: dict[str, BackgroundDispatcher] = {}

    def _dispatcher() -> BackgroundDispatcher:
        if "d" not in holder:
            holder["d"] = BackgroundDispatcher(_max_concurrency())
        return holder["d"]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Initial population of pool list for ``/pools`` command.
        await _populate_pools(deps)
        # Optionally start the polling fallback.
        if os.environ.get("BOT_MODE", "webhook").lower() == "polling":
            stop = asyncio.Event()
            state["polling_stop"] = stop
            app.state.polling_task = asyncio.create_task(
                long_poll_loop(
                    secrets,
                    handler,
                    stop=stop,
                    dispatcher=_dispatcher(),
                    # Same deduper the webhook route uses. Without it a
                    # redelivered update runs the whole fan-out twice.
                    deduper=deduper,
                )
            )
        try:
            yield
        finally:
            stop = state.get("polling_stop")
            if stop is not None:
                stop.set()
            task = getattr(app.state, "polling_task", None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            if "d" in holder:
                await holder["d"].aclose()
            await deps.telegram.aclose()

    app = FastAPI(title="K.H.A.V.I.S. dispatch bot", lifespan=lifespan)
    app.state.dispatcher = _dispatcher

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        h = await router.health_all()
        ok = all(v["ok"] == "yes" for v in h.values())
        return {"status": "ok" if ok else "degraded", "backends": h}

    @app.post("/webhook")
    async def webhook(request: Request) -> dict[str, bool]:
        from .webhook import webhook_route  # local import — keeps startup fast

        return await webhook_route(
            request, secrets, handler, dispatcher=_dispatcher(), deduper=deduper
        )

    @app.post("/webhook/{path_token}")
    async def webhook_with_token(path_token: str, request: Request) -> dict[str, bool]:
        # Reject any URL with a random path suffix unless the secret matches.
        from .webhook import webhook_route  # noqa: PLC0415

        expected = (secrets.webhook_secret or "").encode()
        if not expected or not hmac.compare_digest(path_token.encode(), expected):
            raise HTTPException(status_code=404, detail="not found")
        return await webhook_route(
            request, secrets, handler, dispatcher=_dispatcher(), deduper=deduper
        )

    return app


def _build_handler(router: DispatchRouter, deps: HandlerDeps):
    """Closure that routes an IncomingMessage to its handler."""

    async def handle(incoming: IncomingMessage) -> None:
        # Always log chat_id so the operator can populate ALLOWED_CHAT_IDS
        # when bootstrapping a fresh deployment — but never the message
        # body, which may hold private prompts or pasted secrets.
        print(
            f"[bot.main] incoming chat_id={incoming.chat_id} "
            f"user_id={incoming.user_id} len={len(incoming.text or '')}",
            flush=True,
        )
        handler = route_command(incoming.text or "")
        if handler is None:
            return
        if not is_allowed(incoming.chat_id, deps.allowlist):
            with contextlib.suppress(Exception):
                await deps.telegram.send_message(
                    incoming.chat_id, "🚫 Not authorized.", parse_mode=None
                )
            return
        if handler.__name__ == "handle_run":
            envelope = await handler(incoming, deps)
            if envelope is None or not envelope.prompt:
                return
            # Send a placeholder message first, then edit it as backends
            # complete. This gives the user immediate visual feedback
            # that the dispatch is running, instead of a multi-minute
            # silence when one backend is slow.
            placeholder = "⏳ dispatching..."
            placeholder_msg_id: int | None = None
            try:
                sent = await deps.telegram.send_message(
                    incoming.chat_id,
                    placeholder,
                    parse_mode=None,
                    reply_to=incoming.message_id,
                )
                placeholder_msg_id = sent.get("message_id")
            except Exception as exc:  # noqa: BLE001
                print(f"[bot.main] placeholder send failed: {exc!r}", flush=True)

            last_edit_ts = [0.0]

            async def _on_complete(_new: BackendResult, completed: list[BackendResult]) -> None:
                if placeholder_msg_id is None:
                    return
                # Telegram caps edits at ~30/minute per chat. With three
                # backends we expect at most two updates after the
                # placeholder, well under the limit; the timestamp guard
                # is a belt-and-braces safety net for future backends.
                import time as _t

                now = _t.monotonic()
                if now - last_edit_ts[0] < 1.0:
                    return
                last_edit_ts[0] = now
                # Render a partial report (no consensus yet — that
                # only exists after the final backend completes).
                partial = DispatchReport(
                    envelope=envelope, results=completed, consensus="", consensus_source=""
                )
                with contextlib.suppress(Exception):
                    await deps.telegram.edit_message(
                        incoming.chat_id,
                        placeholder_msg_id,
                        format_report(partial),
                        parse_mode=None,
                    )

            report = await router.dispatch_streaming(envelope, _on_complete)
            try:
                from . import history as bot_history

                bot_history.record(
                    {
                        "kind": "dispatch",
                        "ok": any(r.ok for r in report.results),
                        "prompt": envelope.prompt[:200],
                        "chat_id": envelope.chat_id,
                        "backends": [r.backend for r in report.results],
                        "consensus_source": report.consensus_source,
                    }
                )
            except Exception:  # noqa: BLE001
                pass

            # If at least one backend succeeded, run the judge to pick
            # the best answer. The judge runs locally on Ollama-Mac (or
            # whichever Ollama the surface IP points to), so it costs
            # nothing and adds <2 s in the common case.
            judge = getattr(deps, "judge", None)
            if judge is not None and any(r.ok for r in report.results):
                try:
                    judgment = await judge.judge_pick(envelope, report)
                    if judgment.ok:
                        report.consensus = judgment.text
                        report.consensus_source = "judge"  # type: ignore[assignment]
                    else:
                        # Judge failed — fall back to heuristic synthesis.
                        from .dispatch import synthesize

                        text, source = synthesize(r for r in report.results if r.ok)
                        report.consensus = text
                        report.consensus_source = source
                except Exception as exc:  # noqa: BLE001
                    print(f"[bot.main] judge error: {exc!r}", flush=True)

            if placeholder_msg_id is not None:
                try:
                    await deps.telegram.edit_message(
                        incoming.chat_id,
                        placeholder_msg_id,
                        format_report(report),
                        parse_mode=None,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[bot.main] final edit failed: {exc!r}", flush=True)
            return
        await handler(incoming, deps)

    return handle


# ``app`` is built lazily on first attribute access (PEP 562) so that
# ``python -m bot.main --mode polling`` does not trip webhook-only secret
# validation at import time; ``uvicorn bot.main:app`` still works because
# uvicorn resolves the attribute with getattr().
_APP: FastAPI | None = None


def __getattr__(name: str) -> Any:
    global _APP  # noqa: PLW0603
    if name == "app":
        if _APP is None:
            _APP = _make_app(BotSecrets.from_env())
        return _APP
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Console-script entry point: ``khavis-bot``.
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(prog="khavis-bot")
    parser.add_argument(
        "--mode",
        choices=("webhook", "polling"),
        default=os.environ.get("BOT_MODE", "webhook"),
        help="Listen mode. webhook serves /webhook on $PORT; polling runs a getUpdates loop.",
    )
    parser.add_argument("--host", default=os.environ.get("BOT_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BOT_PORT", "8080")))
    args = parser.parse_args()
    if args.mode == "polling":
        os.environ["BOT_MODE"] = "polling"
        asyncio.run(_run_polling_only())
        return
    os.environ["BOT_MODE"] = "webhook"  # --mode wins over a stale env value
    # webhook mode: fail fast with a clear message if WEBHOOK_SECRET is
    # missing/invalid, before uvicorn wraps the error in an import trace.
    BotSecrets.from_env(mode="webhook")
    import uvicorn  # noqa: PLC0415 - lazy

    uvicorn.run("bot.main:app", host=args.host, port=args.port, log_level="info")


async def _run_polling_only() -> None:
    """Console entry for polling mode (no HTTP server)."""
    secrets = BotSecrets.from_env(mode="polling")
    router, deps = _build_router(secrets)  # type: ignore[arg-type]
    await _populate_pools(deps)
    stop = asyncio.Event()
    dispatcher = BackgroundDispatcher(_max_concurrency())
    try:
        await long_poll_loop(
            secrets,
            _build_handler(router, deps),
            stop=stop,
            dispatcher=dispatcher,
            deduper=UpdateDeduper(),
        )
    finally:
        await dispatcher.aclose()
        await deps.telegram.aclose()


# Defensive re-export so test code can build the router standalone.
def _build_router(secrets: BotSecrets) -> tuple[DispatchRouter, HandlerDeps]:
    return _build_router_and_deps(secrets)


if __name__ == "__main__":
    main()
