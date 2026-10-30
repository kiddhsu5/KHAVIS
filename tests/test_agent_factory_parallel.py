"""Tests for ``agents.agent_factory._PoolRunnable.invoke`` parallel fallback.

Each test stubs out pool resolution + chat so we can prove that
failure of the primary pool does not block the fallback from running
in parallel.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.agent_factory import AgentFactory, _PoolRunnable
from agents.roles import Role


def _make_role() -> Role:
    return Role(
        name="tester",
        display_name="Tester",
        system_prompt="You are a tester.",
        capability_required="程式碼",
        temperature=0.0,
        max_tokens=128,
        max_iterations=1,
        style_hint="",
    )


def _stub_factory(
    primary: MagicMock,
    fallbacks: list[MagicMock],
) -> AgentFactory:
    """Build an AgentFactory backed by mocks. Pools are addressed by name.

    Note: ``MagicMock(name="x")`` exposes ``.name`` as another mock, not
    the string ``"x"`` — so the resolver maps from explicit string keys
    rather than the mock's ``name`` attribute.
    """
    names = ["primary", *[f"fallback_{i}" for i in range(len(fallbacks))]]
    by_name = {names[0]: primary}
    for i, fb in enumerate(fallbacks):
        by_name[names[i + 1]] = fb
    factory = AgentFactory.__new__(AgentFactory)
    factory.registry = MagicMock()
    factory.router = MagicMock()
    factory._cache = {}

    def _resolve(name: str) -> Any:
        return by_name.get(name)

    factory._resolve = _resolve  # type: ignore[method-assign]
    factory._test_names = names  # type: ignore[attr-defined]
    return factory


def _make_runnable(
    factory: AgentFactory,
    primary: MagicMock,
    role: Role,
    fallback_chain: list[str],
) -> _PoolRunnable:
    """Build a runnable that captures the factory directly."""
    return _PoolRunnable(pool=primary, role=role, fallback_chain=fallback_chain, factory=factory)


class TestPoolRunnableParallel:
    def test_first_success_wins(self):
        primary = MagicMock()
        primary.chat.side_effect = RuntimeError("boom")
        fallback = MagicMock()
        fallback.chat.return_value = {"choices": [{"message": {"content": "OK"}}]}

        role = _make_role()
        factory = _stub_factory(primary, [fallback])
        runnable = _make_runnable(factory, primary, role, ["fallback_0"])
        out = runnable.invoke([{"role": "user", "content": "hi"}])
        assert out == "OK"
        assert fallback.chat.called

    def test_all_fail_raises(self):
        primary = MagicMock()
        primary.chat.side_effect = RuntimeError("primary down")
        fb = MagicMock()
        fb.chat.side_effect = RuntimeError("fallback down")

        role = _make_role()
        factory = _stub_factory(primary, [fb])
        runnable = _make_runnable(factory, primary, role, ["fallback_0"])
        with pytest.raises(RuntimeError) as excinfo:
            runnable.invoke([{"role": "user", "content": "hi"}])
        # Last error wins, which is the fallback's.
        assert "fallback down" in str(excinfo.value)

    def test_fallbacks_run_in_parallel(self):
        """When the primary pool sleeps, fallbacks should NOT wait."""
        gate = threading.Event()

        def slow_primary(*_a: Any, **_kw: Any) -> dict[str, Any]:
            gate.wait(timeout=2.0)
            raise RuntimeError("primary timed out")

        primary = MagicMock()
        primary.chat.side_effect = slow_primary
        fallback = MagicMock()
        fallback.chat.return_value = {"choices": [{"message": {"content": "fast"}}]}

        role = _make_role()
        factory = _stub_factory(primary, [fallback])
        runnable = _make_runnable(factory, primary, role, ["fallback_0"])
        t0 = time.perf_counter()
        out = runnable.invoke([])
        elapsed = time.perf_counter() - t0
        assert out == "fast"
        # If the implementation were sequential, this would block on
        # the primary pool's 2-second sleep. Parallel must complete
        # in well under that.
        assert elapsed < 1.0, f"fallback was not parallel (took {elapsed:.2f}s)"

    def test_no_fallback_chain_uses_primary_only(self):
        good = MagicMock()
        good.chat.return_value = {"choices": [{"message": {"content": "primary"}}]}

        role = _make_role()
        factory = _stub_factory(good, [])
        runnable = _make_runnable(factory, good, role, [])
        out = runnable.invoke([])
        assert out == "primary"
        assert good.chat.called

    def test_passes_kwargs_to_chat(self):
        pool = MagicMock()
        pool.chat.return_value = {"choices": [{"message": {"content": "ok"}}]}
        role = _make_role()
        factory = _stub_factory(pool, [])
        runnable = _make_runnable(factory, pool, role, [])
        runnable.invoke([{"role": "user", "content": "x"}], temperature=0.7)
        # Verify kwargs forwarded verbatim.
        kwargs = pool.chat.call_args.kwargs
        assert kwargs["temperature"] == 0.7


class TestEmptyContentIsFailure:
    @pytest.mark.parametrize(
        "empty",
        [
            {"choices": [{"message": {"content": None}}]},
            {"choices": [{"message": {"content": ""}}]},
            {"choices": [{"message": {"content": "   \n"}}]},
            {"choices": []},
            None,
        ],
    )
    def test_empty_primary_does_not_win(self, empty):
        gate = threading.Event()
        primary = MagicMock()
        primary.chat.return_value = empty

        def slower_fallback(*_a: Any, **_kw: Any) -> dict[str, Any]:
            gate.wait(timeout=0.05)  # make sure the empty primary finishes first
            return {"choices": [{"message": {"content": "real answer"}}]}

        fallback = MagicMock()
        fallback.chat.side_effect = slower_fallback
        factory = _stub_factory(primary, [fallback])
        runnable = _make_runnable(factory, primary, _make_role(), ["fallback_0"])
        assert runnable.invoke([]) == "real answer"

    def test_all_empty_raises(self):
        primary = MagicMock()
        primary.chat.return_value = {"choices": [{"message": {"content": None}}]}
        factory = _stub_factory(primary, [])
        runnable = _make_runnable(factory, primary, _make_role(), [])
        with pytest.raises(RuntimeError, match="empty content"):
            runnable.invoke([])

    def test_extract_content_never_returns_literal_none(self):
        from agents.agent_factory import _extract_content

        assert _extract_content({"choices": [{"message": {"content": None}}]}) == ""
        assert _extract_content({"content": None}) == ""
        assert _extract_content({"choices": [{"text": None}]}) == ""
        assert _extract_content(None) == ""
        assert _extract_content({"choices": [{"message": {"content": "hi"}}]}) == "hi"
