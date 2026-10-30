"""Learning node — captures successful patterns into memory.

After verification passes, this node:

* appends an episode to :class:`EpisodicMemory`,
* pushes a short summary into :class:`SemanticMemory`,
* logs attribution for the learner agent.

Router weights are intentionally NOT mutated here — the capability router
remains declarative. The learning node only stores observations for
later offline tuning.

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
from ..memory import (
    get_working_memory,
    recall_similar,
    semantic_search,
    store_episode,
)
from ..roles import load_role
from ..state import TeamState, add_attribution, log_step

log = logging.getLogger(__name__)


def _semantic_context(task: str, k: int = 3) -> list[dict[str, object]]:
    """Fuzzy-match past knowledge against the current task.

    Episodic recall keys on an exact task prefix, so it only fires when the
    same task is literally repeated. Semantic search is the fuzzy complement:
    it surfaces patterns learned from *different* tasks that still apply.
    Degrades to ``[]`` when Chroma is not installed or the store is empty.
    """
    try:
        return semantic_search(task, k=k) or []
    except Exception as exc:  # noqa: BLE001 - semantic memory is best-effort
        log.debug("semantic_search unavailable: %r", exc)
        return []


def learn_node(state: TeamState) -> TeamState:
    """Persist successful patterns and update working memory."""
    session_id = state.get("session_id", "default")
    task = state.get("task", "")
    verification = state.get("verification") or {}
    if not verification.get("passed"):
        # Only learn from successful runs.
        add_attribution(state, agent="learner", node="learn", skipped=True)
        return state

    role = load_role("learner")
    factory = get_agent_factory()
    agent = factory.create_agent(role)

    # Pull related past episodes to ground the learning prompt.
    related = recall_similar("task:" + task[:64], limit=3)
    history_lines = [f"- #{r['id']}: {str(r['value'])[:120]}" for r in related] or [
        "(no prior episodes)"
    ]

    # Fuzzy knowledge from *other* tasks — the part episodic recall cannot see.
    semantic = _semantic_context(task, k=3)
    semantic_lines = [
        f"- {str(hit.get('document') or '')[:160]}" for hit in semantic if hit.get("document")
    ] or ["(no related knowledge yet)"]

    messages = [
        {"role": "system", "content": role.system_prompt},
        {
            "role": "user",
            "content": (
                f"Task:\n{task}\n\n"
                f"Verification:\n{verification}\n\n"
                f"Past similar episodes:\n"
                + "\n".join(history_lines)
                + "\n\nRelated knowledge from other tasks:\n"
                + "\n".join(semantic_lines)
                + "\n\n請輸出 1-2 句可重用 pattern。"
            ),
        },
    ]

    try:
        summary = agent.invoke(
            messages,
            temperature=role.temperature,
            max_tokens=role.max_tokens,
        )
        store_episode(
            key="task:" + task[:64],
            value={
                "task": task,
                "summary": summary,
                "verification": verification,
                "session_id": session_id,
            },
        )
        get_working_memory().set(f"learned:{session_id}", summary)
        log_step(state, node="learn", summary_chars=len(summary))
        add_attribution(state, agent="learner", node="learn", learned_chars=len(summary))
        get_attribution_logger().log_decision(
            session_id=session_id,
            agent="learner",
            node="learn",
            pool=agent.pool.name,
            decision="learned",
            metadata={
                "related_episodes": len(related),
                "related_knowledge": len(semantic),
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("learn node failed")
        state["fallback_used"] = True
        add_attribution(state, agent="learner", node="learn", error=str(exc))
    return state


__all__ = ["learn_node"]
