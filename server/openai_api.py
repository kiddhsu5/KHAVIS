"""OpenAI-compatible drop-in server (v0.5.0).

Exposes the khavis router as a standard OpenAI Chat Completions API so
Cursor / Cline / any OpenAI SDK can point ``base_url`` at us and get
capability routing + failover for free.

    pip install -e .[bot]
    khavis-serve --port 8080
    # or: python -m server.openai_api --port 8080

    export OPENAI_BASE_URL=http://127.0.0.1:8080/v1

Set ``KHAVIS_API_KEY`` to require ``Authorization: Bearer <key>`` on every
``/v1/*`` request. Binding a non-loopback host without a key is refused
unless ``KHAVIS_ALLOW_NO_AUTH=1`` is set explicitly.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import time
import uuid
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, StreamingResponse
except Exception:  # pragma: no cover - optional dep
    FastAPI = None  # type: ignore[assignment,misc]

log = logging.getLogger("khavis.server")

from core import failover as _failover  # noqa: E402

_FailoverUnavailable = _failover.NoHealthyPool

# OpenAI request fields forwarded verbatim to ``plugin.chat`` (after the
# per-provider adaptation in ``_adapt_kwargs``). ``max_tokens`` is handled
# separately because it has a default and is validated.
_FORWARDED_PARAMS = (
    "temperature",
    "top_p",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "user",
)


def _router_stack() -> tuple[Any, Any]:
    """Build (registry, CapabilityRouter) lazily."""
    from core.capability_router import CapabilityRouter
    from core.env import _project_root, config_dir, find_env_file, load_env  # noqa: PLC0415
    from core.registry import PluginRegistry  # noqa: PLC0415

    # `pools.yaml` resolves `env_key` through os.environ, so `.env` has to be
    # in place before discover()/apply_pools_config() or every pool comes back
    # keyless. Only `scripts/start.sh` used to do this — meaning `khavis serve`,
    # the documented entry point, ran with no credentials at all and answered
    # every request with a 401. core.env does not auto-load on import (it would
    # pollute tests), so each entry point must ask; this is the one that was
    # missed.
    if find_env_file():
        load_env()

    cfg = config_dir()
    reg = PluginRegistry(_project_root()).discover()
    pools_yaml = cfg / "pools.yaml"
    if pools_yaml.exists():
        try:
            reg.apply_pools_config(pools_yaml)
        except Exception as exc:  # noqa: BLE001 - serve with discovered defaults
            log.warning("failed to apply %s: %r", pools_yaml, exc)
    else:
        log.warning("%s not found; using discovered pool defaults", pools_yaml)
    router = CapabilityRouter(reg)
    caps_yaml = cfg / "capabilities.yaml"
    if caps_yaml.exists():
        try:
            router.load_capabilities(caps_yaml)
        except Exception as exc:  # noqa: BLE001 - capability routing degrades
            log.warning("failed to load %s: %r", caps_yaml, exc)
    else:
        log.warning("%s not found; capability routing disabled", caps_yaml)
    return reg, router


# ---------------------------------------------------------------------------
# Auth / bind safety
# ---------------------------------------------------------------------------
def _api_key() -> str:
    return os.environ.get("KHAVIS_API_KEY", "").strip()


def _is_loopback(host: str) -> bool:
    host = (host or "").strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind_safety(host: str, api_key: str | None = None) -> None:
    """Refuse to expose an unauthenticated API beyond loopback.

    Anyone who can reach the port could otherwise spend the operator's
    provider quota. ``KHAVIS_ALLOW_NO_AUTH=1`` downgrades the refusal to a
    warning (e.g. when an authenticating reverse proxy sits in front).
    """
    key = _api_key() if api_key is None else api_key
    if key or _is_loopback(host):
        return
    msg = (
        f"khavis-serve: binding {host!r} without KHAVIS_API_KEY exposes an "
        "unauthenticated API that spends your provider quota."
    )
    if os.environ.get("KHAVIS_ALLOW_NO_AUTH", "").strip().lower() in {"1", "true", "yes"}:
        log.warning("%s Continuing because KHAVIS_ALLOW_NO_AUTH is set.", msg)
        return
    raise SystemExit(msg + " Set KHAVIS_API_KEY, bind 127.0.0.1, or set KHAVIS_ALLOW_NO_AUTH=1.")


def _check_auth(request: Any, api_key: str) -> None:
    """Enforce ``Authorization: Bearer <KHAVIS_API_KEY>`` when a key is set."""
    if not api_key:
        return
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        token.strip().encode(), api_key.encode()
    ):
        raise HTTPException(
            status_code=401,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Request parsing helpers
# ---------------------------------------------------------------------------
def _positive_int(body: dict[str, Any], key: str, default: int, *, cap: int | None = None) -> int:
    """``int(body[key])`` with a 400 instead of a 500 on garbage input."""
    raw = body.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise HTTPException(status_code=400, detail=f"{key} must be a positive integer")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{key} must be a positive integer") from None
    if val <= 0:
        raise HTTPException(status_code=400, detail=f"{key} must be a positive integer")
    return min(val, cap) if cap else val


def _sampling_kwargs(body: dict[str, Any]) -> dict[str, Any]:
    """Pick the common OpenAI sampling parameters present in the request."""
    return {k: body[k] for k in _FORWARDED_PARAMS if body.get(k) is not None}


def _adapt_kwargs(plugin: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Translate OpenAI-style kwargs for providers with a different schema.

    Anthropic-format pools (``claude``, ``mimo``) splat extra kwargs into the
    Messages API, which knows ``stop_sequences`` / ``top_p`` but rejects
    ``stop`` and the OpenAI penalty fields. Ollama wants sampling options
    under ``options``. Everything else is OpenAI-compatible and gets the
    kwargs as-is.
    """
    out = dict(kwargs)
    provider = getattr(plugin, "provider_id", None)
    if provider in {"claude", "mimo"}:
        stop = out.pop("stop", None)
        if stop:
            out["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
        for k in ("presence_penalty", "frequency_penalty", "seed", "user"):
            out.pop(k, None)
    elif provider == "ollama":
        options = {k: out.pop(k) for k in ("temperature", "top_p", "stop", "seed") if k in out}
        for k in ("presence_penalty", "frequency_penalty", "user"):
            out.pop(k, None)
        if options:
            out["options"] = options
    return out


def _chat(plugin: Any, messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> Any:
    return plugin.chat(messages, **_adapt_kwargs(plugin, kwargs))


def _usage_int(usage: dict[str, Any], key: str) -> int:
    """Upstream usage fields may be missing *or* ``None``; both count as 0."""
    try:
        return int(usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _normalise_usage(raw: Any) -> dict[str, int]:
    usage = raw if isinstance(raw, dict) else {}
    prompt = _usage_int(usage, "prompt_tokens")
    completion = _usage_int(usage, "completion_tokens")
    total = _usage_int(usage, "total_tokens") or prompt + completion
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _first_choice(result: Any) -> tuple[str, str]:
    """Return ``(content, finish_reason)`` from an upstream result dict."""
    choices = (result.get("choices") if isinstance(result, dict) else None) or []
    if not choices or not isinstance(choices[0], dict):
        return "", "stop"
    first = choices[0]
    content = (first.get("message") or {}).get("content") or ""
    return str(content), str(first.get("finish_reason") or "stop")


def _session_id(request: Any = None) -> str:
    """Budget/routing session for a request.

    ``x-khavis-session`` when the client sends one, else ``KHAVIS_SESSION``.
    Routing (``router.select``) and the budget guard must use the same id,
    or the router filters on a session the guard never charges.
    """
    if request is not None:
        header = str(request.headers.get("x-khavis-session", "")).strip()
        if header:
            return header
    return os.environ.get("KHAVIS_SESSION", "openai-api")


def _sse(obj: dict[str, Any] | str) -> str:
    data = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return f"data: {data}\n\n"


def _error_body(message: str, *, err_type: str, code: int | str | None) -> dict[str, Any]:
    """OpenAI-style error envelope (also used as the SSE error event)."""
    return {"error": {"message": message, "type": err_type, "param": None, "code": code}}


def _pick_plugin(
    router: Any,
    model: str | None,
    messages: list[dict[str, Any]],
    session_id: str | None = None,
):
    """Resolve model name or infer a capability from the prompt text.

    Returns ``(plugin, pool_name, capability)``.
    """
    # 1) explicit pool/model name wins
    if model:
        reg = router.registry
        p = reg.get(model)
        if p is not None:
            return p, model, None
        # model string may match plugin.model
        for plug in reg.all():
            if plug.model == model or model in (plug.list_models() or []):
                return plug, plug.name, None

    # 2) capability heuristic on the first user message
    text = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")
    cap = "英文"
    if any(
        k in text
        for k in ("函式", "函數", "程式", "code", "bug", "refactor", "python", "typescript")
    ):
        cap = "程式碼"
    elif any(k in text for k in ("推理", "prove", "why", "邏輯", "math", "數學")):
        cap = "推理"
    elif any(k in text for k in ("中文", "翻譯")):
        cap = "中文"

    plug = router.select(cap, session_id=session_id or _session_id())
    return plug, (plug.name if plug else None), cap


# Failover lives in core/ so the CLI and this HTTP path share one
# implementation. Keeping a second copy here is what let `khavis chat`
# bypass the breaker entirely.


def _failover_enabled() -> bool:
    return _failover.failover_enabled()


def _call_with_failover(
    router: Any,
    capability: str | None,
    messages: list[dict[str, Any]],
    *,
    pinned: Any = None,
    max_attempts: int = _failover.DEFAULT_MAX_ATTEMPTS,
    session_id: str | None = None,
    **kwargs: Any,
):
    """Call upstream, failing over to the next healthy pool on error.

    Thin adapter over :func:`core.failover.call_with_failover`: the only
    reason it still exists is to translate "no healthy pool" into the 503 the
    HTTP contract promises.
    """
    try:
        return _failover.call_with_failover(
            router,
            capability,
            messages,
            pinned=pinned,
            max_attempts=max_attempts,
            session_id=session_id or _session_id(),
            **kwargs,
        )
    except _FailoverUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"all pools failed for {capability or 'the pinned model'}: {exc}",
        ) from exc


def audit_log(
    capability: str | None,
    plugin: Any,
    pool_name: str | None,
    usage: dict[str, Any],
    cost_usd: float,
    latency_ms: int,
    status: str,
    session_id: str | None,
    error: str | None,
) -> None:
    """Append one audit record (best-effort, never raises)."""
    try:
        from core.audit import log_request  # noqa: PLC0415

        log_request(
            capability=capability,
            pool=pool_name or getattr(plugin, "name", None),
            model=getattr(plugin, "model", None),
            usage=usage,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            status=status,
            session_id=session_id,
            error=error,
        )
    except Exception:  # noqa: BLE001 - audit must never break a request
        pass


def create_app() -> Any:
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("fastapi is required for khavis-serve — pip install -e .[bot]")

    app = FastAPI(title="K.H.A.V.I.S. OpenAI-compatible API", version="0.5.0")
    state: dict[str, Any] = {}
    # Read once at build time so a request can never observe a half-set key.
    api_key = _api_key()

    @app.on_event("startup")
    async def _startup() -> None:
        state["reg"], state["router"] = await asyncio.to_thread(_router_stack)

        # Prime the circuit breaker so the first request does not walk into a
        # pool that was already down before the process started, then keep
        # refreshing in the background: a pool that burns its quota mid-session
        # is marked EXHAUSTED and would otherwise stay dead until restart.
        try:
            from core.env import config_dir  # noqa: PLC0415
            from core.health import get_tracker, load_policy  # noqa: PLC0415

            interval = float(os.environ.get("KHAVIS_HEALTH_REFRESH_S", "60"))
            tracker = get_tracker(load_policy(config_dir() / "health.yaml"))
            pools = list(state["reg"])
            await asyncio.to_thread(tracker.refresh_all, pools)

            async def _refresh_loop() -> None:
                while True:
                    await asyncio.sleep(max(5.0, interval))
                    # Probing hits the network, so keep it off the event loop.
                    await asyncio.to_thread(tracker.refresh_all, pools)

            state["health_task"] = asyncio.create_task(_refresh_loop())
        except Exception as exc:  # noqa: BLE001 - health priming is optional
            log.warning("health priming disabled: %r", exc)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        task = state.pop("health_task", None)
        if task is not None:
            task.cancel()

    @app.get("/v1/models")
    async def models(request: Request) -> dict[str, Any]:
        _check_auth(request, api_key)
        reg = state["reg"]
        data = []
        for p in reg.all():
            for m in p.list_models() or [p.model]:
                if m:
                    data.append(
                        {
                            "id": m,
                            "object": "model",
                            "created": 0,
                            "owned_by": p.provider_id,
                        }
                    )
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        _check_auth(request, api_key)
        # Requiring a JSON content type keeps browsers from firing this as a
        # CORS "simple request" (text/plain form posts skip the preflight).
        ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise HTTPException(status_code=415, detail="Content-Type must be application/json")
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="request body must be a JSON object")
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            raise HTTPException(status_code=400, detail="messages is required")
        if not all(isinstance(m, dict) for m in messages):
            raise HTTPException(status_code=400, detail="each message must be an object")
        stream = bool(body.get("stream"))
        stream_options = body.get("stream_options")
        include_usage = isinstance(stream_options, dict) and bool(
            stream_options.get("include_usage")
        )
        model = body.get("model")
        if model is not None and not isinstance(model, str):
            raise HTTPException(status_code=400, detail="model must be a string")
        max_tokens = body.get("max_tokens")
        if max_tokens is None:
            max_tokens = body.get("max_completion_tokens")
        max_tokens = _positive_int({"max_tokens": max_tokens}, "max_tokens", 1024)
        rag_k = _positive_int(body, "rag_k", 3, cap=50)
        call_kwargs: dict[str, Any] = {"max_tokens": max_tokens, **_sampling_kwargs(body)}

        router = state["router"]
        session_id = _session_id(request)
        # Plugin resolution may touch the registry / health tracker; keep any
        # blocking work off the event loop.
        plugin, pool_name, capability = await asyncio.to_thread(
            _pick_plugin, router, model, messages, session_id
        )
        if plugin is None:
            raise HTTPException(status_code=503, detail="no healthy pool for this request")

        from core.budget import get_guard  # noqa: PLC0415

        guard = get_guard()
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        allowed, reason, reservation = guard.reserve(
            session_id=session_id, pool=plugin.name, prompt_chars=prompt_chars
        )
        if not allowed:
            raise HTTPException(status_code=429, detail=f"budget exceeded: {reason}")

        created = int(time.time())
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        # v0.3.0 RAG: inject retrieved context when a store is available.
        try:
            from core.rag import get_store, inject_context  # noqa: PLC0415

            store = get_store()
            if store.available() and body.get("rag", True):
                user_text = next(
                    (
                        str(m.get("content") or "")
                        for m in reversed(messages)
                        if m.get("role") == "user"
                    ),
                    "",
                )
                hits = await asyncio.to_thread(store.search, user_text, k=rag_k)
                if hits:
                    for i, m in enumerate(messages):
                        if m.get("role") == "user":
                            messages[i] = {
                                **m,
                                "content": inject_context(str(m.get("content") or ""), hits),
                            }
                            break
        except Exception as exc:  # noqa: BLE001 - RAG is optional
            log.debug("RAG skipped: %r", exc)

        def _finish(used_plugin: Any, used_pool: str | None, result: Any, t0: float):
            usage = _normalise_usage(result.get("usage") if isinstance(result, dict) else None)
            rec = guard.record(
                session_id=session_id, pool=used_plugin.name, usage=usage, reservation=reservation
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)
            cost_usd = float(rec.get("cost_usd", 0.0))
            audit_log(
                capability,
                used_plugin,
                used_pool,
                usage,
                cost_usd,
                latency_ms,
                "ok",
                session_id,
                None,
            )
            return usage, cost_usd, latency_ms

        if stream:

            async def gen():
                # Non-streaming upstream: emit one final chunk (compatible enough).
                # Errors after the 200 + headers are already sent cannot change
                # the status code, so report them in-band the way OpenAI does:
                # an ``{"error": ...}`` event followed by ``[DONE]``.
                t0 = time.perf_counter()
                try:
                    used_plugin, result, _attempts = await asyncio.to_thread(
                        _call_with_failover,
                        router,
                        capability,
                        messages,
                        pinned=plugin,
                        session_id=session_id,
                        **call_kwargs,
                    )
                except HTTPException as exc:
                    guard.release(reservation, session_id=session_id)
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    audit_log(
                        capability,
                        plugin,
                        pool_name,
                        {},
                        0.0,
                        latency_ms,
                        "error",
                        session_id,
                        str(exc.detail),
                    )
                    yield _sse(
                        _error_body(str(exc.detail), err_type="server_error", code=exc.status_code)
                    )
                    yield _sse("[DONE]")
                    return
                except Exception as exc:  # noqa: BLE001 - report upstream failure in-band
                    guard.release(reservation, session_id=session_id)
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    audit_log(
                        capability,
                        plugin,
                        pool_name,
                        {},
                        0.0,
                        latency_ms,
                        "error",
                        session_id,
                        repr(exc),
                    )
                    yield _sse(
                        _error_body(f"upstream error: {exc}", err_type="upstream_error", code=502)
                    )
                    yield _sse("[DONE]")
                    return
                used_pool = used_plugin.name
                usage, _cost, _latency = _finish(used_plugin, used_pool, result, t0)
                text, finish_reason = _first_choice(result)
                payload = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model or used_pool,
                    "pool": used_pool,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": text},
                            "finish_reason": finish_reason,
                        }
                    ],
                }
                yield _sse(payload)
                if include_usage:
                    # OpenAI ``stream_options.include_usage``: a trailing
                    # chunk with empty choices that carries the usage.
                    yield _sse(
                        {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model or used_pool,
                            "choices": [],
                            "usage": usage,
                        }
                    )
                yield _sse("[DONE]")

            return StreamingResponse(gen(), media_type="text/event-stream")

        t0 = time.perf_counter()
        try:
            plugin, result, _attempts = await asyncio.to_thread(
                _call_with_failover,
                router,
                capability,
                messages,
                pinned=plugin,
                session_id=session_id,
                **call_kwargs,
            )
            pool_name = plugin.name
        except HTTPException:
            guard.release(reservation, session_id=session_id)
            raise
        except Exception as exc:  # noqa: BLE001 - audit then surface upstream failure
            guard.release(reservation, session_id=session_id)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            audit_log(
                capability, plugin, pool_name, {}, 0.0, latency_ms, "error", session_id, repr(exc)
            )
            raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc
        usage, cost_usd, latency_ms = _finish(plugin, pool_name, result, t0)
        text, finish_reason = _first_choice(result)
        return JSONResponse(
            {
                "id": chunk_id,
                "object": "chat.completion",
                "created": created,
                # Echo the requested model (OpenAI SDKs and some clients key
                # off it); the pool that actually served it is in ``pool``.
                "model": model or pool_name or plugin.name,
                "pool": pool_name or plugin.name,
                "cost_usd": round(cost_usd, 6),
                "latency_ms": latency_ms,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": usage,
            }
        )

    return app


def main() -> None:
    import argparse

    try:
        import uvicorn
    except Exception as exc:  # pragma: no cover
        raise SystemExit("uvicorn is required: pip install 'uvicorn[standard]'") from exc

    parser = argparse.ArgumentParser(prog="khavis-serve")
    parser.add_argument("--host", default=os.environ.get("KHAVIS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KHAVIS_PORT", "8080")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    check_bind_safety(args.host)
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
