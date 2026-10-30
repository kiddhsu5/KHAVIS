"""Bot Phase 4 — phased task execution.

Phases map onto the multi-agent pipeline (planner → coder → debate →
critic → verify → learn) but can also be run as isolated single stages
so the bot can do "just plan this" without burning a full team run.

Public helpers::

    run_phase(name, prompt, on_progress=callback) -> PhaseResult
    run_pipeline(prompt, on_progress=callback) -> PhaseResult
    PHASES  # ordered name → description
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# (name, short label used in Telegram progress messages)
PHASES: tuple[tuple[str, str], ...] = (
    ("plan", "planner"),
    ("code", "coder"),
    ("debate", "debate"),
    ("review", "critic"),
    ("verify", "verifier"),
    ("learn", "learning"),
)

ProgressFn = Callable[[str], Awaitable[None]]


@dataclass
class PhaseResult:
    ok: bool
    phase: str
    output: str = ""
    latency_ms: int = 0
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _pick_output(state: dict[str, Any], phase: str) -> str:
    mapping = {
        "plan": ("plan",),
        "code": ("code_a", "code_b", "plan"),
        "debate": ("debate_log", "code_a", "plan"),
        "review": ("critic_review", "final", "plan"),
        "verify": ("verify_report", "final", "critic_review"),
        "learn": ("learning", "final"),
        "pipeline": ("final", "code_a", "plan", "critic_review"),
    }
    for key in mapping.get(phase, ("final",)):
        val = state.get(key)
        if key == "debate_log" and isinstance(val, list):
            text = "\n\n".join(str(x) for x in val if x)
            if text.strip():
                return text
        elif isinstance(val, str) and val.strip():
            return val
    return str(state.get("final") or state.get("plan") or "")


async def run_phase(
    name: str,
    prompt: str,
    *,
    on_progress: ProgressFn | None = None,
    session_id: str = "phase",
) -> PhaseResult:
    """Run a single pipeline stage (or the full pipeline if ``name=='pipeline'``)."""
    name = (name or "").strip().lower()
    if name == "pipeline":
        return await run_pipeline(prompt, on_progress=on_progress, session_id=session_id)
    if name not in dict(PHASES):
        return PhaseResult(
            ok=False,
            phase=name,
            error=f"unknown phase {name!r}; try one of: {', '.join(p for p, _ in PHASES)}, pipeline",
        )

    if on_progress:
        await on_progress(f"▶️ phase `{name}` starting…")

    t0 = time.perf_counter()

    def _run() -> dict[str, Any]:
        # Isolated stage: reuse the full team graph but tag the task so the
        # planner sees what stage we care about. Single-stage runs go through
        # run_team because the nodes expect a TeamState shaped by the graph.
        from agents import run_team  # noqa: PLC0415 - lazy, heavy imports

        return run_team(f"[phase:{name}] {prompt}", session_id=session_id)

    try:
        loop = asyncio.get_running_loop()
        state = await loop.run_in_executor(None, _run)
        if not isinstance(state, dict):
            state = {}
        output = _pick_output(state, name)
        err = str(state.get("error") or "")
        latency = int((time.perf_counter() - t0) * 1000)
        ok = bool(output) and not err
        if on_progress:
            await on_progress(f"{'✅' if ok else '⚠️'} phase `{name}` done ({latency} ms)")
        return PhaseResult(
            ok=ok,
            phase=name,
            output=output,
            latency_ms=latency,
            error=err,
            meta={"rounds": state.get("rounds", 0)},
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - t0) * 1000)
        if on_progress:
            await on_progress(f"❌ phase `{name}` failed: {exc!r}")
        return PhaseResult(ok=False, phase=name, latency_ms=latency, error=repr(exc))


async def run_pipeline(
    prompt: str,
    *,
    on_progress: ProgressFn | None = None,
    session_id: str = "pipeline",
) -> PhaseResult:
    """Run the full multi-phase team pipeline with per-phase progress."""
    order = [p for p, _ in PHASES]
    if on_progress:
        await on_progress(f"🚀 pipeline start — phases: `{' → '.join(order)}`")

    t0 = time.perf_counter()
    try:
        loop = asyncio.get_running_loop()

        def _run() -> dict[str, Any]:
            from agents import run_team  # noqa: PLC0415

            return run_team(prompt, session_id=session_id)

        state = await loop.run_in_executor(None, _run)
        if not isinstance(state, dict):
            state = {}
        latency = int((time.perf_counter() - t0) * 1000)
        output = _pick_output(state, "pipeline")
        err = str(state.get("error") or "")
        ok = bool(output) and not err
        if on_progress:
            done = " → ".join(order)
            await on_progress(f"{'🏁' if ok else '⚠️'} pipeline done ({latency} ms) — `{done}`")
        return PhaseResult(
            ok=ok,
            phase="pipeline",
            output=output,
            latency_ms=latency,
            error=err,
            meta={
                "rounds": state.get("rounds", 0),
                "phases": order,
                "attribution": (state.get("attribution") or {}),
            },
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - t0) * 1000)
        if on_progress:
            await on_progress(f"❌ pipeline failed: {exc!r}")
        return PhaseResult(ok=False, phase="pipeline", latency_ms=latency, error=repr(exc))
