"""Unit tests for automatic escalation to LangGraph Multi-Agent Team.

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

from unittest.mock import MagicMock, patch

import pytest

from core.escalation import (
    AgentTeamPlugin,
    evaluate_task_complexity,
    extract_task_prompt,
    format_agent_team_response,
)
from core.failover import call_with_failover


class TestTaskComplexityEvaluation:
    def test_simple_prompt_not_complex(self):
        msgs = [{"role": "user", "content": "What is the capital of France?"}]
        is_complex, reason, score = evaluate_task_complexity(msgs)
        assert is_complex is False
        assert score < 0.6

    def test_explicit_capability_trigger(self):
        msgs = [{"role": "user", "content": "hello"}]
        is_complex, reason, score = evaluate_task_complexity(msgs, capability="agent")
        assert is_complex is True
        assert "capability" in reason
        assert score == 1.0

    def test_explicit_parameter_trigger(self):
        msgs = [{"role": "user", "content": "hello"}]
        is_complex, reason, score = evaluate_task_complexity(msgs, escalate=True)
        assert is_complex is True
        assert reason == "explicit_parameter"

    def test_complex_architecture_and_verification_prompt(self):
        prompt = (
            "請幫我規劃並實作一個分佈式快取系統架構設計。\n"
            "1. 提出系統架構圖與模組設計\n"
            "2. 進行多維度優缺點比較與代碼審查\n"
            "3. 撰寫核心邏輯並提供單元測試與驗證邏輯處理邊界條件"
        )
        msgs = [{"role": "user", "content": prompt}]
        is_complex, reason, score = evaluate_task_complexity(msgs)
        assert is_complex is True
        assert score >= 0.6
        assert "architecture" in reason
        assert "verification" in reason

    def test_env_disable(self, monkeypatch):
        monkeypatch.setenv("KHAVIS_AGENT_ESCALATION", "0")
        prompt = "架構設計 規劃並實作 單元測試 代碼審查 重構"
        msgs = [{"role": "user", "content": prompt}]
        is_complex, reason, _ = evaluate_task_complexity(msgs)
        assert is_complex is False
        assert reason == "escalation_disabled_by_env"


class TestAgentTeamFormatting:
    def test_extract_task_prompt(self):
        msgs = [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Task 1"},
            {"role": "assistant", "content": "Done 1"},
            {"role": "user", "content": "Task 2"},
        ]
        task_str = extract_task_prompt(msgs)
        assert "System Instructions:\nBe concise." in task_str
        assert "User: Task 1" in task_str
        assert "Assistant: Done 1" in task_str
        assert "User: Task 2" in task_str

    def test_format_agent_team_response(self):
        mock_state = {
            "final": "```python\ndef hello(): pass\n```",
            "plan": "1. step one\n2. step two",
            "rounds": 2,
            "verification": {"passed": True},
            "attribution": {"records": [{"agent": "planner"}]},
        }
        resp = format_agent_team_response(
            mock_state,
            session_id="test-session",
            prompt_text="build hello",
            escalation_reason="task_complexity",
        )
        assert resp["object"] == "chat.completion"
        assert resp["model"] == "khavis-agent-team"
        assert resp["choices"][0]["message"]["content"] == "```python\ndef hello(): pass\n```"
        assert resp["khavis"]["escalated_to_agents"] is True
        assert resp["khavis"]["debate_rounds"] == 2
        assert resp["khavis"]["verification"]["passed"] is True


class TestAgentTeamPlugin:
    def test_plugin_properties(self):
        plugin = AgentTeamPlugin()
        assert plugin.name == "khavis-agent-team"
        assert "agent" in plugin.capabilities
        assert plugin.health_check()["ok"] is True
        assert plugin.check_quota()["remaining"] > 0
        assert plugin.list_models() == ["khavis-agent-team"]


class TestFailoverEscalation:
    @patch("agents.graph.run_team")
    def test_preflight_escalation_on_complex_task(self, mock_run_team):
        mock_run_team.return_value = {
            "final": "Multi-agent architecture output",
            "plan": "Plan A",
        }
        router = MagicMock()
        msgs = [
            {
                "role": "user",
                "content": (
                    "請進行分散式系統架構設計，規劃並實作核心模組，"
                    "進行深入評審與單元測試以驗證其完整性。"
                ),
            }
        ]

        plugin, result, attempts = call_with_failover(
            router,
            capability=None,
            messages=msgs,
            auto_escalate_agents=True,
        )
        assert plugin.name == "khavis-agent-team"
        assert attempts == 1
        assert "Multi-agent architecture output" in result["choices"][0]["message"]["content"]
        assert result["khavis"]["escalated_to_agents"] is True
        # Single-model router shouldn't have been queried because pre-flight intercepted it
        router.select.assert_not_called()

    @patch("agents.graph.run_team")
    def test_postfailure_escalation_on_quota_exhaustion(self, mock_run_team):
        mock_run_team.return_value = {
            "final": "Salvaged by Agent Team after single pool exhausted",
        }
        # Simulate pool failing with 429
        failing_pool = MagicMock()
        failing_pool.name = "FailingPool"
        failing_pool.chat.side_effect = RuntimeError("429 rate limit quota exceeded")

        router = MagicMock()
        router.select.return_value = failing_pool

        msgs = [{"role": "user", "content": "Simple prompt that hit 429"}]

        plugin, result, attempts = call_with_failover(
            router,
            capability="程式碼",
            messages=msgs,
            max_attempts=1,
            auto_escalate_agents=True,
        )
        assert plugin.name == "khavis-agent-team"
        assert "Salvaged by Agent Team" in result["choices"][0]["message"]["content"]
        assert result["khavis"]["escalation_reason"] == "quota_exhausted"

    def test_no_escalation_when_explicitly_disabled(self):
        failing_pool = MagicMock()
        failing_pool.name = "FailingPool"
        failing_pool.chat.side_effect = RuntimeError("429 rate limit quota exceeded")

        router = MagicMock()
        router.select.return_value = failing_pool

        msgs = [{"role": "user", "content": "Simple prompt"}]

        with pytest.raises(RuntimeError) as exc:
            call_with_failover(
                router,
                capability="程式碼",
                messages=msgs,
                max_attempts=1,
                auto_escalate_agents=False,
            )
        assert "429" in str(exc.value)
