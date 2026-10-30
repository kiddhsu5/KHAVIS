"""Task complexity analysis and automatic escalation to LangGraph Multi-Agent Team.

When a task exceeds single-model complexity (e.g. multi-step architecture design,
code refactoring, or debate/verification requirements) or when candidate pools are
exhausted, this module coordinates seamless escalation to the 7-role LangGraph
orchestration pipeline (Planner -> Coders -> Debater -> Critic -> Verifier -> Learner).

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
import os
import re
import time
import uuid
from typing import Any

from providers.base import ProviderPlugin

log = logging.getLogger(__name__)

#: Threshold for complexity score (0.0 to 1.0) above which tasks are escalated
DEFAULT_COMPLEXITY_THRESHOLD = 0.6

# Regular expressions for identifying multi-agent workload characteristics
_RE_MULTI_STEP = re.compile(
    r"\b(step[- ]by[- ]step|phase|workflow|roadmap|multi[- ]step)\b|"
    r"規劃並實作|逐步實作|分階段|多步驟|執行流程",
    re.IGNORECASE,
)
_RE_ARCHITECTURE = re.compile(
    r"\b(system architecture|architectural design|system design|module design)\b|"
    r"架構設計|系統架構|模組架構|系統設計|架構圖",
    re.IGNORECASE,
)
_RE_DEBATE_CRITIQUE = re.compile(
    r"\b(code review|critique|pros and cons|trade[- ]offs?|debate|compare and contrast)\b|"
    r"代碼審查|架構審查|優缺點比較|權衡|辯論|評審|多維度比較",
    re.IGNORECASE,
)
_RE_VERIFY_TEST = re.compile(
    r"\b(unit tests?|edge cases?|verification|regression tests?|verify and test)\b|"
    r"單元測試|邊界條件|驗證邏輯|全面驗證|回歸測試",
    re.IGNORECASE,
)
_RE_REFACTOR = re.compile(
    r"\b(refactor|deep analysis|codebase overhaul)\b|"
    r"重構|代碼重構|深層分析|全面排查",
    re.IGNORECASE,
)
_RE_ORDERED_LIST = re.compile(
    r"(?:^|\n)\s*(?:1\.|一、|Step 1|\- \[ \]).*?(?:\n\s*(?:2\.|二、|Step 2))",
    re.MULTILINE,
)


def evaluate_task_complexity(
    messages: list[dict[str, Any]],
    capability: str | None = None,
    threshold: float | None = None,
    **kwargs: Any,
) -> tuple[bool, str, float]:
    """Evaluate whether a prompt is complex enough to benefit from LangGraph multi-agent team.

    Returns ``(is_complex, reason, score)``.
    """
    # 1. Explicit capability or parameter triggers
    cap_lower = (capability or "").strip().lower()
    if cap_lower in {"agent", "team", "multi_agent", "workflow", "complex"}:
        return True, f"capability:{cap_lower}", 1.0

    if kwargs.get("escalate") is True or kwargs.get("agent_team") is True:
        return True, "explicit_parameter", 1.0

    # Check env disable
    raw_env = os.environ.get("KHAVIS_AGENT_ESCALATION", "1").strip().lower()
    if raw_env in {"0", "false", "no", "off"}:
        return False, "escalation_disabled_by_env", 0.0

    target_threshold = (
        threshold
        if threshold is not None
        else float(
            os.environ.get("KHAVIS_AGENT_COMPLEXITY_THRESHOLD", str(DEFAULT_COMPLEXITY_THRESHOLD))
        )
    )

    # 2. Text heuristics
    text_fragments: list[str] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            text_fragments.append(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_fragments.append(part.get("text", ""))

    combined_text = "\n".join(text_fragments)
    if not combined_text.strip():
        return False, "empty_prompt", 0.0

    score = 0.0
    matched_reasons: list[str] = []

    if _RE_ARCHITECTURE.search(combined_text):
        score += 0.35
        matched_reasons.append("architecture")

    if _RE_MULTI_STEP.search(combined_text):
        score += 0.35
        matched_reasons.append("multi_step")

    if _RE_DEBATE_CRITIQUE.search(combined_text):
        score += 0.3
        matched_reasons.append("critique")

    if _RE_VERIFY_TEST.search(combined_text):
        score += 0.3
        matched_reasons.append("verification")

    if _RE_REFACTOR.search(combined_text):
        score += 0.25
        matched_reasons.append("refactor")

    if _RE_ORDERED_LIST.search(combined_text):
        score += 0.25
        matched_reasons.append("structured_list")

    # Prompt length factor
    if len(combined_text) >= 1200:
        score += 0.2
        matched_reasons.append("length_gt_1200")

    is_complex = score >= target_threshold
    reason_str = "+".join(matched_reasons) if matched_reasons else "normal"
    return is_complex, reason_str, min(1.0, score)


def extract_task_prompt(messages: list[dict[str, Any]]) -> str:
    """Flatten an OpenAI messages array into a single cohesive task string for agents."""
    system_prompts: list[str] = []
    dialogue: list[str] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        content_str = str(content).strip()
        if not content_str:
            continue

        if role == "system":
            system_prompts.append(content_str)
        elif role == "user":
            dialogue.append(f"User: {content_str}")
        elif role == "assistant":
            dialogue.append(f"Assistant: {content_str}")

    parts: list[str] = []
    if system_prompts:
        parts.append("System Instructions:\n" + "\n".join(system_prompts))
    if dialogue:
        parts.append("\n".join(dialogue))

    return "\n\n".join(parts) if parts else "Perform the requested task."


def format_agent_team_response(
    result: dict[str, Any],
    session_id: str = "default",
    prompt_text: str = "",
    escalation_reason: str = "task_complexity",
) -> dict[str, Any]:
    """Format a LangGraph TeamState dictionary into an OpenAI-compatible chat response."""
    final_content = (
        result.get("final")
        or result.get("code_a")
        or result.get("code_b")
        or result.get("plan")
        or "(No output generated by agent team)"
    )

    # Estimate token usage
    prompt_tokens = max(1, len(prompt_text) // 4)
    completion_tokens = max(1, len(final_content) // 4)

    return {
        "id": f"chatcmpl-agent-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "khavis-agent-team",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": final_content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "khavis": {
            "escalated_to_agents": True,
            "escalation_reason": escalation_reason,
            "session_id": session_id,
            "plan": result.get("plan"),
            "debate_rounds": result.get("rounds", 0),
            "verification": result.get("verification"),
            "attribution": result.get("attribution", {}),
            "fallback_used": result.get("fallback_used", False),
        },
    }


class AgentTeamPlugin(ProviderPlugin):
    """Virtual ProviderPlugin representing the LangGraph 7-role multi-agent team."""

    name = "khavis-agent-team"
    provider_id = "khavis_agent_team"
    default_endpoint = "local://langgraph"
    capabilities = ["agent", "team", "complex", "code", "theory", "debate", "review", "verify"]

    def __init__(self) -> None:
        super().__init__(
            name=self.name,
            endpoint=self.default_endpoint,
            capabilities=self.capabilities,
            metadata={"orchestration": "langgraph", "team_size": 7},
        )

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        task = extract_task_prompt(messages)
        session_id = kwargs.get("session_id", "default")
        from agents.graph import run_team  # noqa: PLC0415

        state = run_team(task=task, session_id=session_id)
        return format_agent_team_response(
            state,
            session_id=session_id,
            prompt_text=task,
            escalation_reason=kwargs.get("escalation_reason", "direct_call"),
        )

    def check_quota(self) -> dict[str, Any]:
        return {
            "remaining": 999999,
            "total": 999999,
            "tier": "orchestrator",
            "provider": self.provider_id,
            "note": "Agent team delegates to member model pools.",
        }

    def list_models(self) -> list[str]:
        return ["khavis-agent-team"]

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "detail": "LangGraph multi-agent team ready"}


def dispatch_to_agent_team(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None = None,
    reason: str = "task_complexity",
    **kwargs: Any,
) -> tuple[AgentTeamPlugin, dict[str, Any], int]:
    """Execute the LangGraph multi-agent pipeline and return ``(plugin, response, attempts)``."""
    task = extract_task_prompt(messages)
    sid = session_id or f"session-{uuid.uuid4().hex[:8]}"

    from agents.graph import run_team  # noqa: PLC0415

    log.info(
        "[escalation] invoking LangGraph multi-agent team for reason: %s (session: %s)", reason, sid
    )
    state = run_team(task=task, session_id=sid)
    response = format_agent_team_response(
        state,
        session_id=sid,
        prompt_text=task,
        escalation_reason=reason,
    )
    return AgentTeamPlugin(), response, 1


__all__ = [
    "AgentTeamPlugin",
    "DEFAULT_COMPLEXITY_THRESHOLD",
    "dispatch_to_agent_team",
    "evaluate_task_complexity",
    "extract_task_prompt",
    "format_agent_team_response",
]
