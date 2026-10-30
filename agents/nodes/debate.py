"""Debate node — runs A vs B rounds, returns continue or escalate.

The debate node is invoked after both coders have produced output. It
records each round as an entry in ``state['debate_log']`` and stops
when the debater has used up ``max_iterations`` rounds. Routing is
decided by returning a string for LangGraph's conditional edges.

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

import logging
from typing import Any

from ..agent_factory import get_agent_factory
from ..attribution import get_attribution_logger
from ..roles import load_role
from ..state import TeamState, add_attribution, log_step

log = logging.getLogger(__name__)


# Returned via the conditional edge ``route_after_debate``.
DEBATE_CONTINUE = "continue"
DEBATE_ESCALATE = "escalate"


def _build_history(debate_log: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for entry in debate_log:
        chunks.append(f"Round {entry['round']}:\n{entry['content']}\n")
    return "\n".join(chunks)


def debate_node(state: TeamState) -> TeamState:
    """Run one debate round and decide whether to continue."""
    code_a = state.get("code_a") or "(empty)"
    code_b = state.get("code_b") or "(empty)"
    session_id = state.get("session_id", "default")
    debate_log: list[dict[str, Any]] = list(state.get("debate_log") or [])

    role = load_role("debater")
    factory = get_agent_factory()
    agent = factory.create_agent(role)

    rounds = int(state.get("rounds", 0)) + 1
    state["rounds"] = rounds

    user_msg = (
        "請以中立辯論主持身份比較以下兩份 Python 程式碼，並列出 3-5 個要點的優缺點。\n\n"
        f"--- Code A ({role.style_hint or 'clean'}) ---\n{code_a}\n\n"
        f"--- Code B (defensive) ---\n{code_b}\n\n"
        f"--- Debate history ---\n{_build_history(debate_log) or '(first round)'}"
    )
    messages = [
        {"role": "system", "content": role.system_prompt},
        {"role": "user", "content": user_msg},
    ]

    try:
        content = agent.invoke(
            messages,
            temperature=role.temperature,
            max_tokens=role.max_tokens,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("debate failed")
        state["error"] = f"debate: {exc!r}"
        state["fallback_used"] = True
        content = "(debate fallback) unable to compare; proceeding to critic."
        add_attribution(state, agent="debater", node="debate", error=str(exc))

    pool = getattr(agent, "pool", None)
    pool_name: str | None = getattr(pool, "name", None) if pool is not None else None
    entry: dict[str, Any] = {
        "round": rounds,
        "content": content,
        "speaker": "debater",
        "pool": pool_name,
    }
    debate_log.append(entry)
    state["debate_log"] = debate_log
    log_step(state, node="debate", round=rounds)
    get_attribution_logger().log_decision(
        session_id=session_id,
        agent="debater",
        node="debate",
        pool=pool_name,
        decision=f"round_{rounds}",
    )

    # Decide route.
    if rounds >= role.max_iterations:
        add_attribution(state, agent="debater", node="debate", route=DEBATE_ESCALATE)
        # LangGraph reads the next edge key from ``__route__`` field.
        state["__route__"] = DEBATE_ESCALATE
    else:
        add_attribution(state, agent="debater", node="debate", route=DEBATE_CONTINUE)
        state["__route__"] = DEBATE_CONTINUE
    return state


def route_after_debate(state: TeamState) -> str:
    """Conditional edge used by :func:`agents.graph.build_graph`."""
    route = state.get("__route__") or DEBATE_ESCALATE
    return route if route in {DEBATE_CONTINUE, DEBATE_ESCALATE} else DEBATE_ESCALATE


__all__ = [
    "debate_node",
    "route_after_debate",
    "DEBATE_CONTINUE",
    "DEBATE_ESCALATE",
]
