"""Coder A / Coder B nodes — produce two distinct implementations.

Both nodes share the same skeleton; the difference is purely the role
configuration (``style_hint`` and ``system_prompt``). They run in
parallel after :func:`planner_node` so the team can compare approaches.

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
import re
from typing import Literal

from ..agent_factory import get_agent_factory
from ..attribution import get_attribution_logger
from ..roles import load_role
from ..state import TeamState, add_attribution, log_step

log = logging.getLogger(__name__)

#: The only state keys a coder node may write its output to.
CodeKey = Literal["code_a", "code_b"]

#: First fenced block in a coder response. The prompts ask for exactly one,
#: but that is a convention rather than a guarantee, so this is deliberately
#: forgiving: a response with no fence is treated as all code.
_FENCE_RE = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)```", re.S)


def split_output(raw: str) -> tuple[str, str]:
    """Split a coder response into ``(code, notes)``.

    The role prompt asks the model to put the implementation in the first
    fenced block and nothing but the unresolved-questions list after it.
    Storing the raw response instead meant ``_solution_markdown()`` wrapped
    the model's prose — role header, rules, summary — inside a ```python
    fence, and handed that to the verifier and to the final deliverable.

    ``notes`` is not discarded: the unresolved questions are exactly what a
    verifier needs, so they are surfaced to it as a labelled block outside
    the code.
    """
    match = _FENCE_RE.search(raw)
    if not match:
        return raw.strip(), ""
    code = match.group(1).strip()
    notes = (raw[: match.start()] + raw[match.end() :]).strip()
    return code, notes


def _write_output(state: TeamState, key: CodeKey, code: str, notes: str) -> None:
    """Write a coder's result to the state under literal keys.

    ``TeamState`` is a ``TypedDict``, so ``state[f"{key}_notes"]`` is a
    dynamic key and mypy rightly rejects it. The mapping is written out
    rather than derived so the two stay in step.
    """
    if key == "code_a":
        state["code_a"] = code
        state["code_a_notes"] = notes
    else:
        state["code_b"] = code
        state["code_b_notes"] = notes


def _produce_code(state: TeamState, role_name: str, output_key: CodeKey) -> TeamState:
    plan = state.get("plan")
    task = state.get("task", "")
    session_id = state.get("session_id", "default")
    if not plan:
        state[output_key] = state.get(output_key) or f"# {role_name}: no plan available"
        add_attribution(state, agent=role_name, node=role_name, error="no plan")
        return state

    role = load_role(role_name)
    factory = get_agent_factory()
    agent = factory.create_agent(role)

    user_msg = (
        f"Plan:\n{plan}\n\n"
        f"Original task:\n{task}\n\n"
        f"Style hint: {role.style_hint}\n\n"
        "請回傳完整 Python source code (含 ```python 區塊)。"
    )
    messages = [
        {"role": "system", "content": role.system_prompt},
        {"role": "user", "content": user_msg},
    ]
    try:
        raw = agent.invoke(
            messages,
            temperature=role.temperature,
            max_tokens=role.max_tokens,
        )
        code, notes = split_output(raw)
        # The unresolved questions are the highest-signal part of the
        # response: they tell the verifier which behaviours were guesses.
        _write_output(state, output_key, code, notes)
        log_step(state, node=role_name, produced_chars=len(code), notes_chars=len(notes))
        get_attribution_logger().log_decision(
            session_id=session_id,
            agent=role_name,
            node=role_name,
            pool=agent.pool.name,
            decision="coded",
            metadata={"style": role.style_hint},
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("%s failed", role_name)
        state["error"] = f"{role_name}: {exc!r}"
        state["fallback_used"] = True
        _write_output(
            state,
            output_key,
            state.get(output_key) or "# generation failed; placeholder",
            "",
        )
        add_attribution(state, agent=role_name, node=role_name, error=str(exc))
    return state


def coder_a_node(state: TeamState) -> TeamState:
    """Coder A: clean / readable implementation."""
    return _produce_code(state, "coder_a", "code_a")


def coder_b_node(state: TeamState) -> TeamState:
    """Coder B: defensive / typed implementation."""
    return _produce_code(state, "coder_b", "code_b")


__all__ = ["coder_a_node", "coder_b_node", "split_output"]
