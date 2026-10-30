"""Unit tests for the multi-agent graph.

These tests cover:

* role loading,
* state helper behaviour,
* node execution with mocked agents,
* routing decisions,
* fallback behaviour.

They intentionally avoid touching the network — every test injects a
fake agent factory so the LLM call is replaced by a deterministic
function.

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

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest  # noqa: E402

from agents import roles as roles_mod  # noqa: E402
from agents import state as state_mod  # noqa: E402
from agents.agent_factory import AgentFactory, set_agent_factory  # noqa: E402
from agents.nodes import (  # noqa: E402
    coder_a_node,
    coder_b_node,
    critic_node,
    debate_node,
    learn_node,
    planner_node,
    verify_node,
)
from agents.nodes.coder import split_output  # noqa: E402
from agents.nodes.debate import (  # noqa: E402
    DEBATE_CONTINUE,
    DEBATE_ESCALATE,
    route_after_debate,
)
from agents.nodes.verifier import (  # noqa: E402
    VERDICTS,
    VERIFY_PASS,
    VERIFY_RETRY,
    VERIFY_TERMINATE,
    route_after_verify,
    route_for_verdict,
    verdict_of,
)


# ----------------------------------------------------------------------
# Fake agent factory
# ----------------------------------------------------------------------
class FakeRunnable:
    def __init__(self, pool, role, answer, fail=False):
        self.pool = pool
        self.role = role
        self.answer = answer
        self.fail = fail

    def invoke(self, messages, **kwargs):  # noqa: D401
        if self.fail:
            raise RuntimeError("simulated failure")
        return self.answer


class FakePool:
    def __init__(self, name="fake-pool"):
        self.name = name
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return {"choices": [{"message": {"content": "ok"}}]}


class FakeFactory(AgentFactory):
    def __init__(self, answers=None, fail_for=None):
        # Skip real registry / router init.
        self.registry = type(
            "R",
            (),
            {"get": lambda self, n: FakePool(n), "by_capability": lambda self, c: [FakePool()]},
        )()
        self.router = type(
            "Router",
            (),
            {
                "candidates": lambda self, c: [FakePool()],
                "select": lambda self, c: FakePool("selected"),
            },
        )()
        self.answers = answers or {}
        self.fail_for = fail_for or set()
        self._cache = {}

    def create_agent(self, role, pool_name=None):
        cache_key = (role.name, pool_name or "default")
        if cache_key in self._cache:
            return self._cache[cache_key]
        if role.name in self.fail_for:
            r = FakeRunnable(FakePool(), role, "", fail=True)
        else:
            answer = self.answers.get(role.name, f"answer-for-{role.name}")
            r = FakeRunnable(FakePool(), role, answer)
        self._cache[cache_key] = r
        return r

    def _resolve(self, pool_name):
        return FakePool(pool_name) if pool_name else FakePool()


@pytest.fixture(autouse=True)
def install_fake_factory():
    set_agent_factory(FakeFactory())
    yield
    set_agent_factory(None)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# State helpers
# ----------------------------------------------------------------------
def test_state_helpers():
    s: state_mod.TeamState = {"task": "x", "attribution": {}}
    state_mod.add_attribution(s, agent="planner", note="first")
    assert s["attribution"]["records"][0]["agent"] == "planner"
    state_mod.log_step(s, node="planner", produced=3)
    assert any(r.get("node") == "planner" for r in s["attribution"]["records"])


# ----------------------------------------------------------------------
# Roles
# ----------------------------------------------------------------------
def test_role_loading():
    planner = roles_mod.load_role("planner")
    assert planner.name == "planner"
    assert planner.capability_required
    assert "planner" in roles_mod.list_roles()


def test_create_role_overrides():
    custom = roles_mod.create_role(
        "planner",
        temperature=0.9,
        system_prompt="custom",
    )
    assert custom.temperature == 0.9
    assert custom.system_prompt == "custom"


# ----------------------------------------------------------------------
# Nodes
# ----------------------------------------------------------------------
def test_planner_node_populates_plan():
    s: state_mod.TeamState = {"task": "do X", "attribution": {"records": []}}
    out = planner_node(s)
    assert "plan" in out and out["plan"]


def test_coder_nodes_run_in_parallel():
    plan_state: state_mod.TeamState = {
        "task": "t",
        "plan": "step 1\nstep 2",
        "attribution": {"records": []},
    }
    a = coder_a_node(dict(plan_state))
    b = coder_b_node(dict(plan_state))
    assert a.get("code_a") and b.get("code_b")


def test_debate_node_routing_continue_then_escalate():
    s: state_mod.TeamState = {
        "task": "t",
        "plan": "p",
        "code_a": "A",
        "code_b": "B",
        "debate_log": [],
        "attribution": {"records": []},
        "rounds": 0,
    }
    first = debate_node(dict(s))
    assert first["__route__"] in {DEBATE_CONTINUE, DEBATE_ESCALATE}
    assert first["rounds"] == 1
    assert first["debate_log"]
    assert route_after_debate(first) == first["__route__"]

    # Force continuation past max_iterations.
    forced: state_mod.TeamState = {
        **first,
        "rounds": 99,
    }
    next_round = debate_node(dict(forced))
    assert next_round["__route__"] == DEBATE_ESCALATE


def test_critic_node_records_review():
    s: state_mod.TeamState = {
        "task": "t",
        "plan": "p",
        "code_a": "A",
        "code_b": "B",
        "critic_review": None,
        "attribution": {"records": []},
    }
    out = critic_node(s)
    assert out.get("critic_review")


def test_verify_node_routes_pass_and_retry():
    # Set FakeFactory answers to JSON that should pass.
    set_agent_factory(
        FakeFactory(answers={"verifier": '{"passed": true, "issues": [], "suggestions": []}'})
    )
    s: state_mod.TeamState = {
        "task": "t",
        "code_a": "A",
        "code_b": "B",
        "critic_review": "ok",
        "attribution": {"records": []},
    }
    out = verify_node(s)
    assert out["__verify_route__"] == VERIFY_PASS
    assert out["verification"]["passed"] is True

    # Now force a failing payload.
    set_agent_factory(
        FakeFactory(answers={"verifier": '{"passed": false, "issues": ["x"], "suggestions": []}'})
    )
    out2 = verify_node(
        {
            "task": "t",
            "code_a": "A",
            "code_b": "B",
            "critic_review": "ok",
            "attribution": {"records": []},
        }
    )
    assert out2["__verify_route__"] == VERIFY_RETRY
    assert out2["verification"]["passed"] is False
    assert route_after_verify(out2) == VERIFY_RETRY


def test_verify_node_fallback_when_factory_fails():
    set_agent_factory(FakeFactory(fail_for={"verifier"}))
    s: state_mod.TeamState = {
        "task": "t",
        "code_a": "A",
        "code_b": "B",
        "critic_review": "ok",
        "attribution": {"records": []},
    }
    out = verify_node(s)
    assert out["fallback_used"] is True
    assert out["verification"]["passed"] is False
    assert out["__verify_route__"] == VERIFY_RETRY


def test_learn_node_no_op_on_failure():
    s: state_mod.TeamState = {
        "task": "t",
        "verification": {"passed": False},
        "attribution": {"records": []},
    }
    out = learn_node(s)
    # When verification failed we should still see the agent record.
    agents = [r.get("agent") for r in out["attribution"]["records"]]
    assert "learner" in agents


def test_learn_node_persists_on_success():
    s: state_mod.TeamState = {
        "task": "task-xyz",
        "verification": {"passed": True, "issues": [], "suggestions": []},
        "attribution": {"records": []},
    }
    out = learn_node(s)
    # At least one attribution record should mention the learner.
    assert any(r.get("agent") == "learner" for r in out["attribution"]["records"])


# ----------------------------------------------------------------------
# Graph assembly (skipped if langgraph not installed)
# ----------------------------------------------------------------------
def test_build_graph_returns_compiled():
    try:
        import langgraph  # noqa: F401  # probe only
    except ImportError:
        pytest.skip("langgraph not installed")
    from agents.graph import build_graph

    g = build_graph()
    assert g is not None
    # Compiled graphs expose ``invoke`` and ``stream``.
    assert hasattr(g, "invoke")
    assert hasattr(g, "stream")


# ----------------------------------------------------------------------
# Review 2026-10-02: bounded critic <-> verifier loop
# ----------------------------------------------------------------------
_FAILING_VERDICT = '{"passed": false, "issues": ["missing tests"], "suggestions": []}'


def _verify_state(attempts: int) -> state_mod.TeamState:
    return {
        "task": "t",
        "code_a": "A",
        "code_b": "B",
        "critic_review": "ok",
        "attribution": {"records": []},
        "verify_attempts": attempts,
    }


def test_verify_attempts_increment_and_retry_below_limit():
    set_agent_factory(FakeFactory(answers={"verifier": _FAILING_VERDICT}))
    out = verify_node(_verify_state(0))
    assert out["verify_attempts"] == 1
    assert out["__verify_route__"] == VERIFY_RETRY
    assert not out.get("fallback_used")


def test_verify_forces_pass_at_limit_with_unverified_final():
    set_agent_factory(FakeFactory(answers={"verifier": _FAILING_VERDICT}))
    limit = roles_mod.load_role("verifier").max_iterations
    assert limit >= 1
    out = verify_node(_verify_state(limit - 1))
    assert out["verify_attempts"] == limit
    assert out["__verify_route__"] == VERIFY_PASS
    assert route_after_verify(out) == VERIFY_PASS
    assert out["fallback_used"] is True
    assert "NOT passed" in out["final"]
    assert "missing tests" in out["final"]
    assert out["verification"]["passed"] is False


def test_verify_error_path_also_bounded():
    set_agent_factory(FakeFactory(fail_for={"verifier"}))
    limit = roles_mod.load_role("verifier").max_iterations
    out = verify_node(_verify_state(limit - 1))
    assert out["__verify_route__"] == VERIFY_PASS
    assert out["fallback_used"] is True
    assert "verifier error" in out["final"]


def test_default_limit_when_role_has_none():
    from agents.nodes.verifier import DEFAULT_MAX_VERIFY_ATTEMPTS, _max_attempts

    class NoLimit:
        max_iterations = 0

    assert _max_attempts(NoLimit()) == DEFAULT_MAX_VERIFY_ATTEMPTS == 3


def test_full_graph_terminates_when_verifier_never_passes():
    try:
        import langgraph  # noqa: F401  # probe only
    except ImportError:
        pytest.skip("langgraph not installed")
    from agents.graph import run_team

    set_agent_factory(FakeFactory(answers={"verifier": _FAILING_VERDICT}))
    out = run_team("never passes", session_id="loop-guard-test")
    limit = roles_mod.load_role("verifier").max_iterations
    assert out["verify_attempts"] == limit
    assert out["fallback_used"] is True
    assert "NOT passed" in (out.get("final") or "")


def test_debate_log_not_duplicated_across_nodes():
    """Nodes return the whole state; debate_log must not re-append on each hop."""
    from langgraph.graph import END, START, StateGraph

    from agents.state import TeamState

    def write(s):
        s["debate_log"] = list(s.get("debate_log") or []) + [{"round": 1}]
        return s

    def passthrough(s):
        s["critic_review"] = "ok"
        return s

    g = StateGraph(TeamState)
    g.add_node("write", write)
    g.add_node("passthrough", passthrough)
    g.add_edge(START, "write")
    g.add_edge("write", "passthrough")
    g.add_edge("passthrough", END)
    out = g.compile().invoke({"task": "t", "debate_log": []})
    assert out["debate_log"] == [{"round": 1}]


# ----------------------------------------------------------------------
# Role config is the single source of truth for prompt text
# ----------------------------------------------------------------------
def test_role_missing_from_yaml_is_announced_not_silently_substituted(tmp_path, caplog):
    """A deleted YAML entry must not quietly fall back to a stale persona.

    ``roles.py`` used to keep a second copy of every ``system_prompt``, so a
    role removed from ``config/agents.yaml`` still loaded — with the old
    instructions and no indication anything was wrong.
    """
    import logging

    partial = tmp_path / "agents.yaml"
    partial.write_text(
        "roles:\n  planner:\n    name: planner\n"
        "    system_prompt: 'configured'\n    capability_required: 推理\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="agents.roles"):
        role = roles_mod.load_role("coder_a", config_path=partial)

    assert any("coder_a" in r.getMessage() for r in caplog.records)
    assert role.name == "coder_a"
    # Structural defaults still come from the table ...
    assert role.capability_required == "程式碼"
    # ... but there is no persona prompt to serve stale.
    assert "Coder A" not in role.system_prompt
    assert "system_prompt" in role.system_prompt


def test_builtin_table_holds_no_prompt_text():
    """Guards the drift surface itself, not just today's symptom."""
    for name, entry in roles_mod._BUILTIN.items():
        assert "system_prompt" not in entry, f"{name} re-introduces a duplicate prompt"


def test_configured_role_still_wins_over_builtin():
    role = roles_mod.load_role("planner")
    yaml_prompt = roles_mod._load_yaml(roles_mod._default_config_path())["planner"]["system_prompt"]
    assert role.system_prompt == yaml_prompt


# ----------------------------------------------------------------------
# Coder output: code and prose are different things
# ----------------------------------------------------------------------
_CODER_RESPONSE = (
    "## 角色：Coder A\n"
    "依收到的 plan 撰寫實作。\n"
    "\n"
    "```python\n"
    "def add(a, b):\n"
    "    return a + b\n"
    "```\n"
    "\n"
    "未解問題\n"
    "假設：空值應回傳 0；影響：add(None, 1) 的結果\n"
)


def test_split_output_separates_code_from_prose():
    code, notes = split_output(_CODER_RESPONSE)
    assert code.strip() == "def add(a, b):\n    return a + b"
    assert "角色" not in code
    assert "未解問題" in notes
    assert "def add" not in notes


def test_split_output_without_a_fence_treats_everything_as_code():
    code, notes = split_output("def f():\n    return 1\n")
    assert code == "def f():\n    return 1"
    assert notes == ""


def test_coder_node_keeps_prose_out_of_the_code():
    set_agent_factory(FakeFactory(answers={"coder_a": _CODER_RESPONSE}))
    out = coder_a_node({"task": "t", "plan": "p", "attribution": {"records": []}})
    assert out["code_a"].strip() == "def add(a, b):\n    return a + b"
    assert "角色" not in out["code_a"]
    assert "未解問題" in out["code_a_notes"]


def test_verifier_receives_the_unresolved_questions():
    """The notes are the coder admitting it guessed; hide them and the
    verifier judges a behaviour it cannot see the uncertainty about."""
    seen = {}

    class SpyFactory(FakeFactory):
        def create_agent(self, role, pool_name=None):
            agent = super().create_agent(role, pool_name=pool_name)
            inner = agent.invoke

            def invoke(messages, **kwargs):
                seen["user"] = messages[-1]["content"]
                return inner(messages, **kwargs)

            agent.invoke = invoke
            return agent

    set_agent_factory(SpyFactory(answers={"verifier": '{"verdict": "PASS"}'}))
    verify_node(
        {
            "task": "t",
            "code_a": "def add(a, b):\n    return a + b",
            "code_a_notes": "假設：空值應回傳 0",
            "code_b": "code-b",
            "critic_review": "ok",
            "attribution": {"records": []},
        }
    )
    assert "假設：空值應回傳 0" in seen["user"]
    assert "Unresolved questions" in seen["user"]


# ----------------------------------------------------------------------
# Three-valued verdict
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"verdict": "PASS"}, "PASS"),
        ({"verdict": "pass"}, "PASS"),  # case-insensitive
        ({"verdict": "FAIL"}, "FAIL"),
        ({"verdict": "PARTIAL"}, "PARTIAL"),
        ({"verdict": "ROLE_MISMATCH"}, "ROLE_MISMATCH"),
        # Legacy shape still routes correctly.
        ({"passed": True}, "PASS"),
        ({"passed": False}, "FAIL"),
        # An explicit verdict wins over a contradictory legacy boolean.
        ({"verdict": "FAIL", "passed": True}, "FAIL"),
        # Unparseable output must never accidentally pass.
        ({"passed": False, "issues": ["could not parse"]}, "FAIL"),
        ({}, "FAIL"),
    ],
)
def test_verdict_of(payload, expected):
    assert verdict_of(payload) == expected


@pytest.mark.parametrize(
    ("verdict", "route"),
    [
        ("PASS", VERIFY_PASS),
        ("FAIL", VERIFY_RETRY),
        ("PARTIAL", VERIFY_TERMINATE),
        ("ROLE_MISMATCH", VERIFY_TERMINATE),
    ],
)
def test_route_for_verdict(verdict, route):
    assert route_for_verdict(verdict) == route
    assert verdict in VERDICTS


def test_verify_node_terminates_on_partial_without_retrying():
    """PARTIAL means a check was blocked. Re-running changes nothing, so
    burning the retry budget on it is pure waste — and the old routing
    treated it as a failure and looped to the limit."""
    set_agent_factory(
        FakeFactory(
            answers={
                "verifier": '{"verdict": "PARTIAL", "issues": [],'
                ' "unverified": ["network egress blocked"], "summary": "could not reach upstream"}'
            }
        )
    )
    out = verify_node(_verify_state(0))
    assert out["__verify_route__"] == VERIFY_TERMINATE
    assert out["fallback_used"] is True
    assert out["verify_attempts"] == 1, "a terminal verdict must not consume retries"
    assert "network egress blocked" in out["final"]
    assert "PARTIAL" in out["final"]


def test_verify_node_terminates_on_role_mismatch():
    set_agent_factory(
        FakeFactory(
            answers={"verifier": '{"verdict": "ROLE_MISMATCH", "unverified": ["asked to fix it"]}'}
        )
    )
    out = verify_node(_verify_state(0))
    assert out["__verify_route__"] == VERIFY_TERMINATE
    assert "role mismatch" in out["final"].lower()
    assert "asked to fix it" in out["final"]


def test_verify_node_normalises_legacy_payload():
    set_agent_factory(FakeFactory(answers={"verifier": '{"passed": true, "issues": []}'}))
    out = verify_node(_verify_state(0))
    assert out["__verify_route__"] == VERIFY_PASS
    # learning.py reads ``passed``; both fields must agree.
    assert out["verification"]["passed"] is True
    assert out["verification"]["verdict"] == "PASS"


def test_route_after_verify_rejects_unknown_routes():
    assert route_after_verify({"__verify_route__": "nonsense"}) == VERIFY_RETRY
    assert route_after_verify({}) == VERIFY_RETRY
    assert route_after_verify({"__verify_route__": VERIFY_TERMINATE}) == VERIFY_TERMINATE


def test_partial_verdict_ends_the_graph_after_one_attempt():
    """End to end: the whole point is that PARTIAL exits to learn, not back
    to critic for another round."""
    try:
        import langgraph  # noqa: F401  # probe only
    except ImportError:
        pytest.skip("langgraph not installed")
    from agents.graph import run_team

    set_agent_factory(
        FakeFactory(
            answers={"verifier": '{"verdict": "PARTIAL", "unverified": ["sandbox has no network"]}'}
        )
    )
    out = run_team("task", session_id="partial-route-test")
    assert out["verify_attempts"] == 1
    assert out["fallback_used"] is True
    assert "sandbox has no network" in (out.get("final") or "")


def test_solution_markdown_keeps_notes_out_of_the_code_fence():
    from agents.nodes.verifier import _solution_markdown

    md = _solution_markdown(
        "def add(a, b):\n    return a + b",
        "def add(a, b):\n    return b + a",
        "review",
        title="Verified solution",
        notes_a="假設：空值應回傳 0",
    )
    fenced = md.split("```")[1]
    assert "假設" not in fenced
    assert "def add(a, b):" in fenced
    assert "假設：空值應回傳 0" in md
