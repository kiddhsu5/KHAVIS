"""Judge backend: a cheap LLM picks the best of the other backends' answers.

After the main fan-out completes, this backend receives every other
backend's ``BackendResult`` (via ``DispatchReport.results``) and asks
a small local model (``qwen2.5:1.5b`` by default — the same model
already used on Surface Ollama, so no extra setup) to choose the best
answer. The chosen text is returned as ``consensus`` and ``consensus_source``
becomes ``"judge"`` so the aggregator renders it as the headline.

If the judge model itself fails or returns unusable output, we fall
back to the deterministic ``synthesize`` heuristic (longest answer
≥ 20 chars wins), so the user still gets *some* answer.
"""

from __future__ import annotations

import asyncio
import json
import time

from ..models import BackendResult, DispatchEnvelope, DispatchReport
from .base import Backend, HealthResult


def _pick_longest(candidates: list[BackendResult]) -> tuple[str, str]:
    """Inline heuristic fallback when the judge model fails.

    Mirrors ``bot.dispatch.synthesize`` but inlined here to avoid a
    circular import (judge ← backends/__init__ ← dispatch).
    """
    items = [r for r in candidates if r.ok and r.text.strip()]
    if not items:
        return "", ""
    items.sort(key=lambda r: len(r.text), reverse=True)
    top = items[0]
    if len(top.text) >= 20:
        return top.text, top.backend
    return items[0].text, items[0].backend


JUDGE_SYSTEM_PROMPT = (
    "You are a strict evaluator comparing candidate answers to a user's "
    "prompt. Read the prompt and each candidate carefully, then output a "
    "single JSON object on one line of the form "
    '{"best": <0-based index of the best candidate>, "reason": "<one sentence>"}. '
    "Choose the candidate that most directly and correctly addresses the "
    "prompt. Do not add commentary outside the JSON."
)


class JudgeBackend(Backend):
    """Reads the completed ``DispatchReport`` and picks the best candidate.

    Not registered in ``DEFAULT_BACKEND_ORDER`` — the orchestrator
    invokes it separately after the main fan-out completes (see
    ``main.py::_orchestrate_run``).
    """

    name = "judge"

    def __init__(
        self,
        binary: str = "ollama",
        model: str = "qwen2.5:1.5b",
        timeout_s: int = 60,
        base_url: str | None = None,
    ) -> None:
        self._binary = binary
        self._model = model
        self._timeout = timeout_s
        self._base_url = base_url or "http://localhost:11434"

    async def health(self) -> HealthResult:
        # Probe the configured Ollama endpoint + the model.
        import shutil

        binary = shutil.which(self._binary)
        if not binary:
            return HealthResult(False, f"{self._binary} not in PATH")
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self._base_url}/api/tags")
                if r.status_code != 200:
                    return HealthResult(False, f"ollama HTTP {r.status_code}")
                data = r.json()
                names = [m.get("name") for m in data.get("models") or []]
                if not any(n and n.startswith(self._model.split(":")[0]) for n in names):
                    return HealthResult(False, f"model {self._model} not pulled")
                return HealthResult(True, f"ollama ready, model={self._model}")
        except Exception as exc:  # noqa: BLE001
            return HealthResult(False, repr(exc))

    async def run(self, envelope: DispatchEnvelope) -> BackendResult:
        # Backend.run is only called when the orchestrator decides to
        # invoke the judge. In our actual flow the judge reads from the
        # shared ``last_report`` populated by the orchestrator (see main.py).
        # This fallback path is for direct invocation / tests.
        raise NotImplementedError("JudgeBackend.run is not callable directly — use judge_pick()")

    async def judge_pick(
        self,
        envelope: DispatchEnvelope,
        report: DispatchReport,
    ) -> BackendResult:
        """Read the orchestrator's partial result and return a chosen text."""
        candidates = [r for r in report.results if r.ok and r.text.strip()]
        if not candidates:
            return BackendResult(
                backend="judge",
                ok=False,
                error="no successful backend to judge",
            )
        if len(candidates) == 1:
            # No choice to make; just echo the only result.
            winner = candidates[0]
            return BackendResult(
                backend="judge",
                ok=True,
                text=winner.text,
                model=self._model,
                latency_ms=0,
                extra={"picked_from": winner.backend, "reason": "only candidate"},
            )

        prompt = envelope.prompt[:1500]
        # Number each candidate for the judge to reference.
        numbered = "\n\n".join(
            f"[{i}] backend={r.backend} model={r.model}\n{r.text[:1500]}"
            for i, r in enumerate(candidates)
        )
        user_msg = (
            f"User prompt:\n{prompt}\n\nCandidate answers:\n{numbered}\n\n"
            "Output exactly one JSON line."
        )

        try:
            t0 = time.perf_counter()
            # urllib is blocking; keep it off the event loop so other
            # chats keep being served while the judge thinks.
            raw = await asyncio.to_thread(self._ollama_chat, user_msg)
            latency_ms = int((time.perf_counter() - t0) * 1000)
        except Exception as exc:  # noqa: BLE001
            # Fallback to heuristic — never fail the user because the judge broke.
            text, source = _pick_longest(candidates)
            return BackendResult(
                backend="judge",
                ok=False,
                text=text,
                error=f"judge model failed ({exc!r}); fell back to {source}",
                model=self._model,
                latency_ms=0,
                extra={"fallback_source": source},
            )

        winner_idx, reason = _parse_judge_pick(raw, len(candidates))
        if winner_idx is None or winner_idx >= len(candidates):
            # Bad JSON — fall back to heuristic.
            text, source = _pick_longest(candidates)
            return BackendResult(
                backend="judge",
                ok=False,
                text=text,
                error=f"judge returned unusable JSON: {raw[:120]!r}; fell back to {source}",
                model=self._model,
                latency_ms=latency_ms,
                extra={"fallback_source": source},
            )

        winner = candidates[winner_idx]
        return BackendResult(
            backend="judge",
            ok=True,
            text=winner.text,
            model=self._model,
            latency_ms=latency_ms,
            extra={
                "picked_from": winner.backend,
                "reason": reason or "(no reason given)",
            },
        )

    def _ollama_chat(self, user_msg: str) -> str:
        """Sync ``ollama chat`` call — run in a thread executor."""
        import json
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"{self._base_url}/api/chat",
            data=json.dumps(
                {
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    "stream": False,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"ollama chat failed: {exc!r}") from exc
        msg = (body.get("message") or {}) if isinstance(body, dict) else {}
        return (msg.get("content") or "").strip()


def _parse_judge_pick(raw: str, n_candidates: int) -> tuple[int | None, str]:
    """Pull ``{"best": <int>, "reason": "..."}`` out of the judge output.

    Tolerant: tolerates ```json fences, trailing prose, etc.
    """
    if not raw:
        return None, ""
    # Strip code fences if present.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.splitlines() if not line.startswith("```")
        ).strip()
    # Find the JSON object braces.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None, ""
    try:
        obj = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None, ""
    if not isinstance(obj, dict):
        return None, ""
    idx = obj.get("best")
    if not isinstance(idx, int) or not 0 <= idx < n_candidates:
        return None, ""
    reason = obj.get("reason")
    return idx, str(reason) if isinstance(reason, str) else ""


__all__ = ["JudgeBackend", "_parse_judge_pick"]
