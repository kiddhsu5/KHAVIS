"""Tests for the ``khavis`` console script (core/cli.py).

The CLI is the first thing anyone touches, so these focus on what a new user
sees and does. The two bugs they pin down were both "the tool reported a state
that was not true":

* ``doctor`` ran every ``health_check`` before ``.env`` was loaded, so a fully
  configured install reported ``0 pool(s) ready`` while nine of its keys sat
  unused in the very file it had just located.
* the pool table was a wall of identical ``unhealthy`` lines, which says
  nothing — the actionable question is "what can I use, and why not the rest".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import cli as cli_mod
from core.cli import (
    _auth_hint,
    _explain_upstream_failure,
    _looks_like_auth_failure,
    main,
)


class _Health:
    """Minimal stand-in for ProviderPlugin.health_check()."""

    def __init__(self, ok: bool, detail: str = ""):
        self.ok = ok
        self.detail = detail


class _Plugin:
    def __init__(self, name: str, ok: bool, detail: str = ""):
        self.name = name
        self._h = _Health(ok, detail)
        self.chat_calls: list[list[dict[str, str]]] = []
        self._reply: str = "hello"

    def health_check(self) -> dict:
        return {"ok": self._h.ok, "detail": self._h.detail}

    def chat(self, messages, **kwargs):
        self.chat_calls.append(messages)
        return {"choices": [{"message": {"content": self._reply}}], "model": "m", "usage": {}}


class _Registry(list):
    def __init__(self, plugins):
        super().__init__(plugins)
        self.applied: list[Path] = []

    def discover(self):
        return self

    def apply_pools_config(self, path):
        self.applied.append(path)

    def errors(self):
        return []

    def all(self):
        return list(self)

    def get(self, name):
        return next((p for p in self if p.name == name), None)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point the CLI at a temp config dir with a fake registry + no .env."""
    import core.env as env_mod

    monkeypatch.setattr(env_mod, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(env_mod, "find_env_file", lambda: None)
    monkeypatch.setattr(env_mod, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(cli_mod, "load_env", lambda *a, **k: None, raising=False)
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "pools.yaml").write_text("pools: []\n", encoding="utf-8")
    (cfg / "capabilities.yaml").write_text("capabilities: []\n", encoding="utf-8")
    return tmp_path


def _install(monkeypatch, plugins):
    reg = _Registry(plugins)

    class _Router:
        def select(self, capability, **kwargs):
            for p in plugins:
                if getattr(p, "_routable", False):
                    return p
            return None

        def select_any(self, **kwargs):
            return self.select("")

        def explain_unavailable(self, capability):
            return "no pool advertises this capability"

    monkeypatch.setattr(cli_mod, "_load_router", lambda cfg: (reg, _Router()))
    return reg


# ----------------------------------------------------------------------
# doctor: .env must be loaded before probing
# ----------------------------------------------------------------------
def test_doctor_loads_env_before_probing(wired, monkeypatch, capsys):
    """Regression: probes ran against an empty os.environ.

    ``health_check`` reports ``key_present`` by reading os.environ, so probing
    before ``load_env()`` makes every configured pool look keyless.
    """
    called: list[bool] = []

    def _fake_load_env(*_a, **_k):
        called.append(True)

    monkeypatch.setattr("core.env.load_env", _fake_load_env)
    monkeypatch.setattr("core.env.find_env_file", lambda: wired / ".env")
    ready = _Plugin("Good", True, "key_present=True")
    _install(monkeypatch, [ready])

    main(["doctor"])
    out = capsys.readouterr().out
    assert called, "doctor must load .env before calling health_check"
    assert "1 pool(s) ready" in out


def test_doctor_reports_ready_pools_first(wired, monkeypatch, capsys):
    ok_pool = _Plugin("Working", True, "endpoint=https://x")
    _install(monkeypatch, [ok_pool])

    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "1 pool(s) ready to use" in out
    assert "+ Working" in out


def test_doctor_separates_missing_key_from_rejected_key(wired, monkeypatch, capsys):
    good = _Plugin("Good", True, "key_present=True")
    missing = _Plugin("Missing", False, "endpoint=https://x key_present=False")
    rejected = _Plugin("Rejected", False, "Error code: 401 - authorized_error")
    _install(monkeypatch, [good, missing, rejected])

    main(["doctor"])
    out = capsys.readouterr().out
    assert "no API key" in out
    assert "Missing" in out
    # A present-but-refused key is a different problem and must not be filed
    # under "you forgot to set the key" — that misdirects the fix.
    assert "rejected their API key" in out
    assert "Rejected" in out
    assert "rotate it" in out


def test_doctor_fails_when_nothing_is_usable(wired, monkeypatch, capsys):
    """A report ending "all checks passed" while nothing can serve a request
    is the most misleading thing this command can print."""
    _install(monkeypatch, [_Plugin("Missing", False, "key_present=False")])

    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "all checks passed" not in out
    assert "NO pool can serve a request" in out


# ----------------------------------------------------------------------
# chat: the one-command path
# ----------------------------------------------------------------------
def test_chat_sends_prompt_and_prints_reply(wired, monkeypatch, capsys):
    pool = _Plugin("Good", True, "key_present=True")
    pool._routable = True
    pool._reply = "the answer is 42"
    _install(monkeypatch, [pool])

    assert main(["chat", "what is the answer"]) == 0
    out = capsys.readouterr().out
    assert "the answer is 42" in out
    assert pool.chat_calls[0][0]["content"] == "what is the answer"


def test_chat_honours_explicit_pool(wired, monkeypatch, capsys):
    a = _Plugin("A", True, "")
    a._routable = True
    b = _Plugin("B", True, "")
    _install(monkeypatch, [a, b])

    assert main(["chat", "--pool", "B", "hi"]) == 0
    assert b.chat_calls and not a.chat_calls


def test_chat_rejects_unknown_pool_with_a_useful_list(wired, monkeypatch, capsys):
    a = _Plugin("OnlyThis", True, "")
    _install(monkeypatch, [a])

    assert main(["chat", "--pool", "Nope", "hi"]) == 1
    err = capsys.readouterr().err
    assert "unknown pool" in err
    assert "OnlyThis" in err, "should list the pools that do exist"


def test_chat_explains_no_healthy_pool(wired, monkeypatch, capsys):
    _install(monkeypatch, [])

    assert main(["chat", "hi"]) == 1
    err = capsys.readouterr().err
    assert "no healthy pool" in err
    assert "khavis doctor" in err, "must point at the command that diagnoses it"


def test_chat_summarises_a_401_instead_of_dumping_the_body(wired, monkeypatch, capsys):
    pool = _Plugin("P", True, "")

    def _boom(messages, **kwargs):
        raise RuntimeError(
            "Error code: 401 - {'type': 'error', 'error': {'type': 'authorized_error', "
            "'message': \"login fail: Please carry the API secret key in the 'Authorization' "
            "field of the request header (1004)\"}, 'request_id': '071825bcd54928fb0a39998e693d11c1'}"
        )

    pool.chat = _boom  # type: ignore[method-assign]
    pool._routable = True
    _install(monkeypatch, [pool])

    assert main(["chat", "hi"]) == 1
    err = capsys.readouterr().err
    assert "401" in err
    assert "key rejected" in err
    assert "request_id" not in err, "must not dump the raw upstream body"
    assert len(err) < 300, "the explanation should be one actionable line"


def test_chat_quiet_suppresses_the_meta_line(wired, monkeypatch, capsys):
    pool = _Plugin("Good", True, "")
    pool._routable = True
    _install(monkeypatch, [pool])

    assert main(["chat", "--quiet", "hi"]) == 0
    assert "pool=Good" not in capsys.readouterr().err


def test_chat_json_emits_raw_response(wired, monkeypatch, capsys):
    pool = _Plugin("Good", True, "")
    pool._routable = True
    _install(monkeypatch, [pool])

    assert main(["chat", "--json", "hi"]) == 0
    out = capsys.readouterr().out
    assert '"choices"' in out
    assert '"model": "m"' in out


# ----------------------------------------------------------------------
# Failure classification
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "detail",
    [
        "Error code: 401 - authorized_error",
        "Error code: 403 - permission denied",
        "invalid_api_key",
        "Invalid API key provided",
    ],
)
def test_auth_failures_are_recognised(detail):
    assert _looks_like_auth_failure(detail) is True


@pytest.mark.parametrize(
    "detail",
    [
        "ConnectionError(MaxRetryError('Connection refused'))",
        "Error code: 404 - model not found",
        "endpoint=http://x:11434 error=timeout",
    ],
)
def test_non_auth_failures_are_not_misclassified(detail):
    assert _looks_like_auth_failure(detail) is False


def test_auth_hint_is_specific():
    assert "expired" in _auth_hint("Error code: 401")
    assert "lacks access" in _auth_hint("403 - permission denied")


def test_upstream_explanation_never_leaks_the_credential():
    """The failure text comes from an upstream body; it must not be echoed
    verbatim when it contains anything sensitive."""
    explanation = _explain_upstream_failure("P", "401 unauthorized sk-secret-abc123")
    assert "sk-secret-abc123" not in explanation


# ----------------------------------------------------------------------
# core/failover: the CLI must get mechanism 1 too
# ----------------------------------------------------------------------
class TestFailover:
    def _router(self, pools):
        class _R:
            def __init__(self, _p):
                self.pools = _p

            def select(self, capability, **kw):
                excluded = set(kw.get("exclude") or ())
                for p in self.pools:
                    if p.name not in excluded:
                        return p
                return None

            def select_any(self, **kw):
                excluded = set(kw.get("exclude") or ())
                for p in self.pools:
                    if p.name not in excluded:
                        return p
                return None

            def explain_unavailable(self, capability):
                return "all excluded"

        return _R(pools)

    def test_dead_first_pool_falls_over_to_the_next(self, monkeypatch):
        from core import failover as fo
        from core.health import PoolHealthTracker, reset_tracker

        reset_tracker(
            PoolHealthTracker.__call__
            and __import__("core.health", fromlist=["HealthPolicy"]).HealthPolicy(
                failure_threshold=1
            )
        )

        class Dead(_Plugin):
            def chat(self, messages, **kwargs):
                raise RuntimeError("Error code: 401 - unauthorized")

        dead = Dead("Dead", True, "")
        alive = _Plugin("Alive", True, "")
        used, _result, attempts = fo.call_with_failover(
            self._router([dead, alive]), "推理", [{"role": "user", "content": "x"}]
        )
        assert used is alive, "a 401 on the first pool must not end the request"
        assert attempts == 2

    def test_pinned_pool_still_fails_over(self, monkeypatch):
        """Regression: `khavis chat` called plugin.chat() directly and so had
        none of the breaker — an expired key on the chosen pool surfaced raw."""
        from core import failover as fo
        from core.health import HealthPolicy, reset_tracker

        reset_tracker(HealthPolicy(failure_threshold=1))

        class Dead(_Plugin):
            def chat(self, messages, **kwargs):
                raise RuntimeError("boom")

        dead, alive = Dead("Pinned", True, ""), _Plugin("Other", True, "")
        used, _r, attempts = fo.call_with_failover(
            self._router([alive]), None, [{"role": "user", "content": "x"}], pinned=dead
        )
        assert used is alive
        assert attempts == 2

    def test_failure_is_recorded_on_the_breaker(self):
        """The whole point of routing through failover: the dead pool must be
        recorded so later requests skip it."""
        from core import failover as fo
        from core.health import HealthPolicy, get_tracker, reset_tracker

        tracker = reset_tracker(HealthPolicy(failure_threshold=1)) or get_tracker()

        class Dead(_Plugin):
            def chat(self, messages, **kwargs):
                raise RuntimeError("boom")

        dead, alive = Dead("Dead2", True, ""), _Plugin("Alive2", True, "")
        fo.call_with_failover(
            self._router([dead, alive]), "推理", [{"role": "user", "content": "x"}]
        )
        assert tracker.get("Dead2").consecutive_failures >= 1

    def test_all_pools_failing_raises_the_last_error(self):
        from core import failover as fo
        from core.health import HealthPolicy, reset_tracker

        reset_tracker(HealthPolicy(failure_threshold=1))

        class Dead(_Plugin):
            def __init__(self, name, msg):
                super().__init__(name, True, "")
                self._msg = msg

            def chat(self, messages, **kwargs):
                raise RuntimeError(self._msg)

        a, b = Dead("A", "first"), Dead("B", "last")
        with pytest.raises(RuntimeError, match="last"):
            fo.call_with_failover(self._router([a, b]), "推理", [{"role": "user", "content": "x"}])

    def test_no_healthy_pool_raises_no_healthy_pool(self):
        from core import failover as fo
        from core.health import HealthPolicy, reset_tracker

        reset_tracker(HealthPolicy(failure_threshold=1))
        with pytest.raises(fo.NoHealthyPool):
            fo.call_with_failover(self._router([]), "推理", [{"role": "user", "content": "x"}])

    def test_khavis_failover_zero_pins_strictly(self, monkeypatch):
        from core import failover as fo
        from core.health import HealthPolicy, reset_tracker

        monkeypatch.setenv("KHAVIS_FAILOVER", "0")
        reset_tracker(HealthPolicy(failure_threshold=1))

        class Dead(_Plugin):
            def chat(self, messages, **kwargs):
                raise RuntimeError("boom")

        dead, alive = Dead("Pinned2", True, ""), _Plugin("Other2", True, "")
        with pytest.raises(RuntimeError, match="boom"):
            fo.call_with_failover(
                self._router([alive]), None, [{"role": "user", "content": "x"}], pinned=dead
            )


# ----------------------------------------------------------------------
# doctor --deep: must not report a working pool as broken
# ----------------------------------------------------------------------
def test_deep_verify_calls_each_ready_pool(wired, monkeypatch, capsys):
    good = _Plugin("Good", True, "")
    reg = _Registry([good])

    class _Router:
        def select(self, capability, **kw):
            return good

    monkeypatch.setattr(cli_mod, "_load_router", lambda cfg: (reg, _Router()))
    main(["doctor", "--deep"])
    out = capsys.readouterr().out
    assert "1 pool(s) verified working" in out
    assert "✓ Good" in out


def test_deep_verify_does_not_cap_max_tokens(wired, monkeypatch, capsys):
    """Regression, caught by running it for real.

    The probe originally passed ``max_tokens=16``. Reasoning models spend that
    whole budget internally and return an empty content string, which
    ``raise_if_empty`` converts into "this pool is broken" — so Google-Gemini
    and OpenAI-API, both of which answer fine, were reported as dead. A
    verifier that misreports working pools is worse than none: it tells the
    operator to delete them.
    """
    seen: list[dict] = []

    class Recorder(_Plugin):
        def chat(self, messages, **kwargs):
            seen.append(kwargs)
            return super().chat(messages, **kwargs)

    good = Recorder("Good", True, "")
    reg = _Registry([good])

    class _Router:
        def select(self, capability, **kw):
            return good

    monkeypatch.setattr(cli_mod, "_load_router", lambda cfg: (reg, _Router()))
    main(["doctor", "--deep"])
    assert seen, "the probe must actually call the pool"
    assert "max_tokens" not in seen[0], (
        "a small max_tokens makes reasoning models look broken; the probe must not set one"
    )


def test_deep_verify_reports_a_pool_that_raises(wired, monkeypatch, capsys):
    class Broken(_Plugin):
        def chat(self, messages, **kwargs):
            raise RuntimeError("404 model not found")

    reg = _Registry([Broken("Broken", True, "")])

    class _Router:
        def select(self, capability, **kw):
            return Broken("Broken", True, "")

    monkeypatch.setattr(cli_mod, "_load_router", lambda cfg: (reg, _Router()))
    main(["doctor", "--deep"])
    out = capsys.readouterr().out
    assert "0 pool(s) verified working" in out
    assert "✗ Broken" in out
