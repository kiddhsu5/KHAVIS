"""Critic node — sharp second-pass review.

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

from ..agent_factory import get_agent_factory
from ..attribution import get_attribution_logger
from ..roles import load_role
from ..state import TeamState, add_attribution, log_step

log = logging.getLogger(__name__)


def critic_node(state: TeamState) -> TeamState:
    """Produce ``state['critic_review']``."""
    session_id = state.get("session_id", "default")
    code_a = state.get("code_a") or ""
    code_b = state.get("code_b") or ""
    plan = state.get("plan") or ""
    debate = state.get("debate_log") or []

    role = load_role("critic")
    factory = get_agent_factory()
    agent = factory.create_agent(role)

    messages = [
        {"role": "system", "content": role.system_prompt},
        {
            "role": "user",
            "content": (
                "Plan:\n"
                f"{plan}\n\n"
                "Code A:\n"
                f"{code_a}\n\n"
                "Code B:\n"
                f"{code_b}\n\n"
                "Debate log:\n"
                f"{debate}\n\n"
                "請以銳利眼光列出安全性、效能、可維護性的具體 issue 與建議。"
            ),
        },
    ]
    try:
        review = agent.invoke(
            messages,
            temperature=role.temperature,
            max_tokens=role.max_tokens,
        )
        state["critic_review"] = review
        log_step(state, node="critic", produced_chars=len(review))
        get_attribution_logger().log_decision(
            session_id=session_id,
            agent="critic",
            node="critic",
            pool=agent.pool.name,
            decision="reviewed",
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("critic failed")
        state["error"] = f"critic: {exc!r}"
        state["fallback_used"] = True
        state["critic_review"] = state.get("critic_review") or "fallback: no review"
        add_attribution(state, agent="critic", node="critic", error=str(exc))
    return state


__all__ = ["critic_node"]
