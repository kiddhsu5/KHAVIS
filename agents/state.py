"""LangGraph TeamState definition and attribution helpers.

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

from typing import Annotated, Any, TypedDict


def _last_wins(existing: Any, new: Any) -> Any:
    """Reducer that keeps the most recent value.

    LangGraph's default for ``Optional[str]``-shaped keys raises
    ``InvalidUpdateError`` when two nodes write to the same key in one
    step. Since every node in this graph already produces a final value
    (rather than incrementally building one), last-write-wins is the
    correct merge.
    """
    return new


class TeamState(TypedDict, total=False):
    """Shared state object that flows through every node in the team graph.

    Every mutable key carries an explicit ``Annotated[...]`` reducer so
    that LangGraph never raises ``InvalidUpdateError`` when two nodes
    write to the same key in one step (planner → fallback sets the same
    field that the happy path also sets, debate node rewrites the
    debate log, etc.).
    """

    # ---- inputs (immutable per step) ----
    task: Annotated[str, _last_wins]
    session_id: Annotated[str, _last_wins]

    # ---- intermediate artefacts (last write wins) ----
    plan: Annotated[str | None, _last_wins]
    code_a: Annotated[str | None, _last_wins]
    code_b: Annotated[str | None, _last_wins]
    #: Everything the coder said outside the code block — in practice the
    #: unresolved-questions list. Kept out of ``code_a``/``code_b`` so the
    #: deliverable stays runnable, and handed to the verifier separately
    #: because those questions are exactly what it needs to judge.
    code_a_notes: Annotated[str | None, _last_wins]
    code_b_notes: Annotated[str | None, _last_wins]
    # Nodes return the whole state, and the debate node writes the full
    # history list — so an appending reducer would re-add every entry on
    # each node transition. Last-write-wins keeps exactly one copy.
    debate_log: Annotated[list[dict[str, Any]], _last_wins]
    critic_review: Annotated[str | None, _last_wins]
    verification: Annotated[dict[str, Any] | None, _last_wins]

    # ---- output (last write wins) ----
    final: Annotated[str | None, _last_wins]

    # ---- meta ----
    attribution: Annotated[dict[str, Any], _last_wins]
    error: Annotated[str | None, _last_wins]
    rounds: Annotated[int, _last_wins]
    #: How many times the verifier has run in this session. Bounds the
    #: critic <-> verifier retry loop (see ``agents.nodes.verifier``).
    verify_attempts: Annotated[int, _last_wins]
    fallback_used: Annotated[bool, _last_wins]

    # ---- routing (read by conditional edges) ----
    # Declared so LangGraph keeps them in the channel set (undeclared keys
    # are dropped from node updates) and so mypy accepts the writes.
    __route__: Annotated[str | None, _last_wins]
    __verify_route__: Annotated[str | None, _last_wins]


def add_attribution(state: TeamState, **record: Any) -> TeamState:
    """Append an attribution record to ``state['attribution']``.

    Each record is keyed by the agent/role that produced it. The function
    creates the attribution dict when missing so callers don't have to
    initialise the state themselves.
    """
    attribution = dict(state.get("attribution") or {})
    bucket: list[dict[str, Any]] = list(attribution.get("records", []))
    bucket.append(dict(record))
    attribution["records"] = bucket
    state["attribution"] = attribution
    return state


def log_step(state: TeamState, node: str, **payload: Any) -> TeamState:
    """Attach a generic step log entry to the attribution log."""
    return add_attribution(
        state,
        node=node,
        type="step",
        **payload,
    )


__all__ = ["TeamState", "add_attribution", "log_step"]
