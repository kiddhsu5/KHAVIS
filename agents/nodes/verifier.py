"""Verifier node — independent cross-pool validation.

The verifier uses a *different* pool than the coder/debater pool to
catch blind spots. It returns a dict shaped like::

    {"passed": bool, "issues": [...], "suggestions": [...]}

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..agent_factory import get_agent_factory
from ..attribution import get_attribution_logger
from ..roles import load_role
from ..state import TeamState, add_attribution, log_step

log = logging.getLogger(__name__)


VERIFY_PASS = "pass"
VERIFY_RETRY = "retry"
#: A required check was blocked by the environment, or the request is out of
#: the verifier's remit. Retrying cannot change either, so this terminates
#: the loop and hands back the best effort instead of burning the budget.
VERIFY_TERMINATE = "terminate"

#: Verdicts the verifier may report. ``PASS``/``FAIL`` are the ordinary
#: outcomes; ``PARTIAL`` means a required check was blocked, and
#: ``ROLE_MISMATCH`` means verifying was the wrong job for this request.
VERDICTS = ("PASS", "FAIL", "PARTIAL", "ROLE_MISMATCH")
_TERMINATING = ("PARTIAL", "ROLE_MISMATCH")

#: Verifier runs allowed when the role does not set ``max_iterations``.
DEFAULT_MAX_VERIFY_ATTEMPTS = 3


def _max_attempts(role: Any) -> int:
    try:
        value = int(getattr(role, "max_iterations", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value >= 1 else DEFAULT_MAX_VERIFY_ATTEMPTS


def _solution_markdown(
    code_a: str,
    code_b: str,
    review: str,
    *,
    title: str,
    notes_a: str = "",
    notes_b: str = "",
) -> str:
    def _notes(label: str, body: str) -> str:
        return f"\n## Unresolved questions — {label}\n{body}\n" if body else ""

    return (
        f"# {title}\n\n"
        f"## Code A\n```python\n{code_a}\n```\n"
        f"{_notes('Code A', notes_a)}"
        f"\n## Code B\n```python\n{code_b}\n```\n"
        f"{_notes('Code B', notes_b)}"
        f"\n## Review\n{review}\n"
    )


def _unverified_note(payload: dict[str, Any], attempts: int) -> str:
    issues = payload.get("issues") or []
    if not isinstance(issues, list):
        issues = [issues]
    lines = "\n".join(f"- {issue}" for issue in issues) or "- (no issues reported)"
    return (
        f"\n## Verification NOT passed\n"
        f"Verifier did not pass the solution after {attempts} attempt(s); "
        f"returning the best effort. Outstanding issues:\n{lines}\n"
    )


def _terminate_note(payload: dict[str, Any], verdict: str) -> str:
    """Explain a non-retryable stop so the caller is not left guessing."""
    unverified = payload.get("unverified") or []
    if not isinstance(unverified, list):
        unverified = [unverified]
    reasons = "\n".join(f"- {item}" for item in unverified) or "- (not stated)"
    if verdict == "ROLE_MISMATCH":
        headline = "## Verification not attempted (role mismatch)"
    else:
        headline = "## Verification incomplete (PARTIAL)"
    return (
        f"\n{headline}\n"
        f"The verifier reported {verdict}. This is terminal rather than a failure: the "
        f"retry loop was not re-entered because re-running would not change the "
        f"outcome. Still unverified:\n{reasons}\n"
    )


def verdict_of(payload: dict[str, Any]) -> str:
    """Normalise a verifier payload to one of :data:`VERDICTS`.

    Prefers the explicit ``verdict`` field. Falls back to the older boolean
    ``passed`` so payloads produced before the three-valued contract still
    route correctly — and so an unparseable response, which
    :func:`_parse_payload` turns into ``passed: False``, lands on FAIL rather
    than accidentally passing.
    """
    raw = payload.get("verdict")
    if isinstance(raw, str) and raw.strip().upper() in VERDICTS:
        return raw.strip().upper()
    return "PASS" if payload.get("passed") is True else "FAIL"


def route_for_verdict(verdict: str) -> str:
    """Map a verdict onto the graph's conditional-edge vocabulary."""
    if verdict == "PASS":
        return VERIFY_PASS
    if verdict in _TERMINATING:
        return VERIFY_TERMINATE
    return VERIFY_RETRY


def _parse_payload(content: str) -> dict[str, Any]:
    """Best-effort parse of the verifier's JSON output."""
    if not content:
        return {"passed": False, "issues": ["empty response"], "suggestions": []}
    # Try to locate a JSON block.
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {
        "passed": False,
        "issues": ["could not parse verifier output"],
        "suggestions": [content[:512]],
        "raw": content,
    }


def _select_alt_pool(factory, role, primary_name: str):
    """Pick a different pool than ``primary_name`` for cross-validation."""
    candidates = factory.router.candidates(role.capability_required)
    for plugin in candidates:
        if plugin.name != primary_name:
            return plugin
    return None


def verify_node(state: TeamState) -> TeamState:
    """Produce ``state['verification']`` and decide pass/retry."""
    session_id = state.get("session_id", "default")
    task = state.get("task", "")
    code_a = state.get("code_a") or ""
    code_b = state.get("code_b") or ""
    notes_a = state.get("code_a_notes") or ""
    notes_b = state.get("code_b_notes") or ""
    review = state.get("critic_review") or ""

    role = load_role("verifier")
    factory = get_agent_factory()
    primary = factory.create_agent(role)

    # Cross-validation: try a different pool if available.
    alt = _select_alt_pool(factory, role, primary.pool.name)
    chosen = primary
    pool_used = primary.pool.name
    if alt is not None:
        try:
            chosen = factory.create_agent(role, pool_name=alt.name)
            pool_used = alt.name
        except Exception:
            chosen = primary
            pool_used = primary.pool.name

    def _notes_block(label: str, body: str) -> str:
        # The coders' unresolved questions are the highest-signal input here:
        # each one is a behaviour they guessed at rather than derived.
        return (
            f"\nUnresolved questions raised by {label} (claims, verify them):\n{body}\n"
            if body
            else ""
        )

    messages = [
        {"role": "system", "content": role.system_prompt},
        {
            "role": "user",
            "content": (
                f"Original task:\n{task}\n\n"
                f"Code A:\n{code_a}\n"
                f"{_notes_block('Coder A', notes_a)}"
                f"\nCode B:\n{code_b}\n"
                f"{_notes_block('Coder B', notes_b)}"
                f"\nCritic review:\n{review}\n\n"
                "請獨立驗證並以 JSON 格式回答。"
            ),
        },
    ]
    attempts = int(state.get("verify_attempts") or 0) + 1
    state["verify_attempts"] = attempts
    limit = _max_attempts(role)

    try:
        content = chosen.invoke(
            messages,
            temperature=role.temperature,
            max_tokens=role.max_tokens,
        )
        payload = _parse_payload(content)
        verdict = verdict_of(payload)
        # Normalise both fields so every downstream consumer (``learning.py``
        # reads ``passed``; attribution reads the decision) sees the same
        # decision no matter which shape the model emitted.
        payload["verdict"] = verdict
        payload["passed"] = verdict == "PASS"
        state["verification"] = payload

        route = route_for_verdict(verdict)
        state["__verify_route__"] = route
        log_step(
            state, node="verifier", passed=payload["passed"], verdict=verdict, attempt=attempts
        )
        get_attribution_logger().log_decision(
            session_id=session_id,
            agent="verifier",
            node="verifier",
            pool=pool_used,
            decision=route,
        )

        if route == VERIFY_PASS:
            state["final"] = _solution_markdown(
                code_a,
                code_b,
                review,
                title="Verified solution",
                notes_a=notes_a,
                notes_b=notes_b,
            )
        elif route == VERIFY_TERMINATE:
            # Not a failure and not retryable — surface exactly what is
            # unverified instead of pretending the solution was judged.
            state["fallback_used"] = True
            state["final"] = _solution_markdown(
                code_a,
                code_b,
                review,
                title=f"Unverified solution ({verdict})",
                notes_a=notes_a,
                notes_b=notes_b,
            ) + _terminate_note(payload, verdict)
            add_attribution(
                state,
                agent="verifier",
                node="verifier",
                route=route,
                reason=f"verifier reported {verdict}",
            )
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("verifier failed")
        state["error"] = f"verifier: {exc!r}"
        state["fallback_used"] = True
        state["verification"] = {
            "passed": False,
            "verdict": "FAIL",
            "issues": [f"verifier error: {exc!r}"],
            "suggestions": [],
        }
        state["__verify_route__"] = VERIFY_RETRY
        add_attribution(state, agent="verifier", node="verifier", error=str(exc))

    if state.get("__verify_route__") == VERIFY_RETRY and attempts >= limit:
        # Retry budget spent: stop the critic <-> verifier loop and hand back
        # the best effort, clearly marked as unverified.
        payload = state.get("verification") or {}
        state["__verify_route__"] = VERIFY_PASS
        state["fallback_used"] = True
        state["final"] = _solution_markdown(
            code_a,
            code_b,
            review,
            title="Unverified solution (verifier retry limit reached)",
            notes_a=notes_a,
            notes_b=notes_b,
        ) + _unverified_note(payload, attempts)
        add_attribution(
            state,
            agent="verifier",
            node="verifier",
            route=VERIFY_PASS,
            reason=f"max verify attempts reached ({attempts}/{limit})",
        )
    return state


def route_after_verify(state: TeamState) -> str:
    """Conditional edge used by :func:`agents.graph.build_graph`.

    An unrecognised or missing route falls back to ``retry``, which is the
    conservative choice: retrying is bounded, while passing an unjudged
    deliverable is not.
    """
    route = state.get("__verify_route__") or VERIFY_RETRY
    known = {VERIFY_PASS, VERIFY_RETRY, VERIFY_TERMINATE}
    return route if route in known else VERIFY_RETRY


__all__ = [
    "verify_node",
    "route_after_verify",
    "verdict_of",
    "route_for_verdict",
    "VERIFY_PASS",
    "VERIFY_RETRY",
    "VERIFY_TERMINATE",
    "VERDICTS",
]
