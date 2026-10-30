"""In-process backend that runs the K.H.A.V.I.S. multi-agent team.

Drives ``agents.run_team`` (planner → coder_a / coder_b → debate →
critic → verify → learn) for a single user prompt, returning the
final synthesised answer plus attribution. The graph runs
synchronously under the hood, so we off-load it to a thread executor
to keep the FastAPI event loop free.
"""

from __future__ import annotations

import asyncio
import time
from functools import partial

from ..models import BackendResult, DispatchEnvelope
from .base import Backend, HealthResult


class KhavisBackend(Backend):
    """Runs ``agents.run_team`` against the K.H.A.V.I.S. pool registry."""

    name = "khavis"

    def __init__(self, session_id: str = "bot") -> None:
        self._session_id = session_id
        self._lock = asyncio.Lock()

    async def health(self) -> HealthResult:
        # Lightweight: just confirm the registry can be built.
        try:
            from core.registry import PluginRegistry  # noqa: PLC0415

            reg = PluginRegistry().discover()
            return HealthResult(True, f"{len(reg)} pools registered")
        except Exception as exc:  # noqa: BLE001
            return HealthResult(False, repr(exc))

    async def run(self, envelope: DispatchEnvelope) -> BackendResult:
        from core.budget import get_guard  # noqa: PLC0415

        guard = get_guard()
        session_id = f"chat-{envelope.chat_id}"
        res = guard.allow(
            session_id=session_id, pool="khavis", prompt_chars=len(envelope.prompt or "")
        )
        # ``allow`` returns either a bool (+ ``guard.reason()``) or ``(ok, reason)``.
        allowed, reason = res if isinstance(res, tuple) else (bool(res), "")
        if not allowed:
            return BackendResult(
                backend="khavis",
                ok=False,
                error=f"budget exceeded: {reason or guard.reason()}",
            )

        from ._http_fallback import (  # noqa: PLC0415
            call_upstream_llm_fallback,
            has_any_upstream_key,
        )

        if envelope.workspace_dir and has_any_upstream_key():
            loop = asyncio.get_running_loop()
            t_ag = time.perf_counter()
            fb = await loop.run_in_executor(
                None,
                call_upstream_llm_fallback,
                envelope.prompt,
                "LangGraph-7-Role",
                "khavis",
                65,
                envelope.workspace_dir,
                True,
                envelope.thinking_mode,
                envelope.thinking_parameter,
            )
            if fb.get("ok"):
                return BackendResult(
                    backend="khavis",
                    ok=True,
                    text=str(fb["text"]),
                    model=str(fb.get("model") or "LangGraph-7-Role"),
                    latency_ms=int((time.perf_counter() - t_ag) * 1000),
                    tokens_in=int(fb.get("tokens_in") or 0),
                    tokens_out=int(fb.get("tokens_out") or 0),
                    extra={"steps": "7", "autonomous_tools": "True"},
                )

        try:
            from agents import run_team  # noqa: PLC0415 - lazy

            loop = asyncio.get_running_loop()
            t0 = time.perf_counter()
            state = await loop.run_in_executor(
                None,
                partial(
                    run_team,
                    envelope.prompt,
                    session_id=self._session_id,
                ),
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)
            try:
                # attribution records carry per-node token estimates when available
                attr = state.get("attribution") if isinstance(state, dict) else {}
                records = (attr or {}).get("records") or []
                tok = sum(int(r.get("tokens") or 0) for r in records if isinstance(r, dict))
                guard.record(
                    session_id=session_id,
                    pool="khavis",
                    usage={"prompt_tokens": tok, "completion_tokens": 0},
                )
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            from ._http_fallback import (  # noqa: PLC0415
                call_upstream_llm_fallback,
                has_any_upstream_key,
            )

            if has_any_upstream_key():
                loop = asyncio.get_running_loop()
                t_fb = time.perf_counter()
                fb = await loop.run_in_executor(
                    None,
                    call_upstream_llm_fallback,
                    envelope.prompt,
                    "LangGraph-7-Role",
                    "khavis",
                    30,
                    envelope.workspace_dir,
                    True,
                    envelope.thinking_mode,
                    envelope.thinking_parameter,
                )
                if fb.get("ok"):
                    return BackendResult(
                        backend="khavis",
                        ok=True,
                        text=str(fb["text"]),
                        model=str(fb.get("model") or "LangGraph-7-Role"),
                        latency_ms=int((time.perf_counter() - t_fb) * 1000),
                        tokens_in=int(fb.get("tokens_in") or 0),
                        tokens_out=int(fb.get("tokens_out") or 0),
                        extra={"steps": "7", "fallback_used": "True"},
                    )
            return BackendResult(
                backend="khavis",
                ok=False,
                error=repr(exc),
            )

        # Extract the canonical answer from the team state. Prefer the
        # synthesised ``final``; fall back to whichever stage has output.
        if not isinstance(state, dict):
            state = {}
        try:
            final = (state.get("final") or "").strip()
            if not final:
                final = (state.get("code_a") or state.get("plan") or "").strip()
            error_msg = state.get("error") or ""
            rounds = state.get("rounds", 0)
            attribution = state.get("attribution") or {}
            records = (attribution.get("records") if isinstance(attribution, dict) else None) or []
            plan = state.get("plan")
            code_b = state.get("code_b")
            fallback_used = state.get("fallback_used", False)
        except Exception as exc:  # noqa: BLE001
            return BackendResult(
                backend="khavis",
                ok=False,
                error=f"malformed team state: {exc!r}",
            )

        if not final:
            return BackendResult(
                backend="khavis",
                ok=False,
                error=error_msg or "team produced no answer",
                model="run_team",
                extra={"rounds": str(rounds)},
            )

        extra = {
            "rounds": str(rounds),
            "fallback_used": str(fallback_used),
            "steps": str(len(records)),
        }
        if plan:
            extra["has_plan"] = "1"
        if code_b:
            extra["two_coders"] = "1"

        return BackendResult(
            backend="khavis",
            ok=True,
            text=final,
            model="agents.run_team",
            latency_ms=latency_ms,
            extra=extra,
        )
