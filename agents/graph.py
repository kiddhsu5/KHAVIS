r"""LangGraph StateGraph definition for the multi-agent team.

Flow::

    planner -> coder_a + coder_b (parallel)
                       |
                       v
                    debate <----+
                  /        \    |
            continue        escalate
                |              |
                v              v
             (loop)         critic -> verify -> learn -> END
                              ^         |
                              +---------+ (retry, at most verifier
                                     |     max_iterations runs; then
                                  (pass)   forced pass + fallback_used)

A :class:`MemorySaver` checkpointer is wired in by default; callers can
swap in another backend (e.g. ``SqliteSaver``) via ``build_graph``.

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

log = logging.getLogger(__name__)


def build_graph(checkpointer: Any = None):
    """Build and return a compiled :class:`langgraph.graph.StateGraph`.

    Imports are deferred so the module can be loaded even when langgraph
    isn't installed (useful for syntax checks).
    """
    try:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.graph import END, START, StateGraph
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "langgraph is not installed; run `pip install langgraph` to use "
            "agents.graph.build_graph"
        ) from exc

    from .nodes import (
        coder_a_node,
        coder_b_node,
        critic_node,
        debate_node,
        learn_node,
        planner_node,
        verify_node,
    )
    from .nodes.debate import DEBATE_CONTINUE, DEBATE_ESCALATE, route_after_debate
    from .nodes.verifier import VERIFY_PASS, VERIFY_RETRY, VERIFY_TERMINATE, route_after_verify
    from .state import TeamState

    graph = StateGraph(TeamState)

    # Nodes
    graph.add_node("planner", planner_node)
    graph.add_node("coder_a", coder_a_node)
    graph.add_node("coder_b", coder_b_node)
    graph.add_node("debate", debate_node)
    graph.add_node("critic", critic_node)
    graph.add_node("verifier", verify_node)
    graph.add_node("learn", learn_node)

    # Edges
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "coder_a")
    graph.add_edge("planner", "coder_b")
    # Both coders fan-in to debate.
    graph.add_edge("coder_a", "debate")
    graph.add_edge("coder_b", "debate")

    graph.add_conditional_edges(
        "debate",
        route_after_debate,
        {
            DEBATE_CONTINUE: "debate",
            DEBATE_ESCALATE: "critic",
        },
    )
    graph.add_edge("critic", "verifier")
    graph.add_conditional_edges(
        "verifier",
        route_after_verify,
        {
            VERIFY_PASS: "learn",
            VERIFY_RETRY: "critic",
            # PARTIAL / ROLE_MISMATCH: a required check was blocked, or
            # verifying was the wrong job. Re-running cannot change either,
            # so this exits to learn rather than burning the retry budget.
            VERIFY_TERMINATE: "learn",
        },
    )
    graph.add_edge("learn", END)

    cp = checkpointer if checkpointer is not None else MemorySaver()
    return graph.compile(checkpointer=cp)


# ----------------------------------------------------------------------
# Convenience runner
# ----------------------------------------------------------------------
def run_team(
    task: str,
    *,
    session_id: str = "default",
    config: dict[str, Any] | None = None,
    checkpointer: Any = None,
):
    """Build the graph and invoke it once with ``task`` as input.

    Returns the final state dict. Streaming variants can be implemented by
    the caller using ``compiled.stream(...)``.
    """
    compiled = build_graph(checkpointer=checkpointer)
    initial: dict[str, Any] = {
        "task": task,
        "session_id": session_id,
        "debate_log": [],
        "attribution": {"records": []},
        "rounds": 0,
        "verify_attempts": 0,
        "fallback_used": False,
    }
    cfg = {"configurable": {"thread_id": session_id}}
    if config:
        cfg.update(config)
    return compiled.invoke(initial, config=cfg)


__all__ = ["build_graph", "run_team"]
