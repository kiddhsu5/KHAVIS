"""Planner node — decomposes the user's task into a numbered plan.

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
from typing import Any, cast

from ..agent_factory import get_agent_factory
from ..attribution import get_attribution_logger
from ..roles import load_role
from ..state import TeamState, add_attribution, log_step

log = logging.getLogger(__name__)


def planner_node(state: TeamState) -> TeamState:
    """Produce ``state['plan']`` from ``state['task']``."""
    task = state.get("task", "")
    session_id = state.get("session_id", "default")
    if not task:
        state["error"] = "planner: empty task"
        add_attribution(state, agent="planner", node="planner", error="empty task")
        return state

    role = load_role("planner")
    factory = get_agent_factory()
    agent = factory.create_agent(role)

    messages = [
        {"role": "system", "content": role.system_prompt},
        {"role": "user", "content": f"Task:\n{task}\n\n請輸出 markdown 編號步驟。"},
    ]
    try:
        plan = agent.invoke(messages, temperature=role.temperature, max_tokens=role.max_tokens)
        state["plan"] = plan
        log_step(state, node="planner", produced_chars=len(plan))
        get_attribution_logger().log_decision(
            session_id=session_id,
            agent="planner",
            node="planner",
            pool=agent.pool.name,
            decision="planned",
            metadata={"task_chars": len(task)},
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("planner failed")
        state["error"] = f"planner: {exc!r}"
        state["fallback_used"] = True
        state["plan"] = state.get("plan") or f"# Fallback plan\n\n- Solve: {task}\n- Test it\n"
        add_attribution(state, agent="planner", node="planner", error=str(exc))
    # Return only the fields we mutated — returning the entire state dict
    # triggers LangGraph's "Can receive only one value per step" reducer
    # error on read-only keys like ``task`` and ``session_id``.
    raw = cast(dict[str, Any], state)
    keep = {"plan", "error", "fallback_used", "attribution"}
    return cast(TeamState, {k: v for k, v in raw.items() if k in keep})


__all__ = ["planner_node"]
