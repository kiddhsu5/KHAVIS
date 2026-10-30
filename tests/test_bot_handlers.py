"""Unit tests for the bot handlers (command parsing)."""

from __future__ import annotations

from bot.handlers import (
    handle_run,
    is_allowed,
    parse_run_command,
    route_command,
)
from bot.models import IncomingMessage


class TestIsAllowed:
    def test_empty_allowlist_denies_everyone(self):
        assert is_allowed(123, []) is False

    def test_allowlist_accepts_match(self):
        assert is_allowed(123, [123, 456]) is True

    def test_allowlist_rejects_nonmatch(self):
        assert is_allowed(789, [123, 456]) is False


class TestParseRun:
    def test_basic_prompt(self):
        only, cap, prompt = parse_run_command("/run hello world")
        assert only is None
        assert cap is None
        assert prompt == "hello world"

    def test_only_subset(self):
        only, cap, prompt = parse_run_command("/run --only codex,claude ping")
        assert only == ["codex", "claude"]
        assert cap is None
        assert prompt == "ping"

    def test_capability_flag(self):
        only, cap, prompt = parse_run_command("/run --capability code fix bug")
        assert only is None
        assert cap == "code"
        assert prompt == "fix bug"

    def test_capability_equals_form(self):
        only, cap, prompt = parse_run_command("/run --capability=code fix bug")
        assert cap == "code"
        assert prompt == "fix bug"

    def test_only_with_capability(self):
        only, cap, prompt = parse_run_command("/run --only codex --capability=reasoning explain")
        assert only == ["codex"]
        assert cap == "reasoning"
        assert prompt == "explain"

    def test_empty_prompt(self):
        only, cap, prompt = parse_run_command("/run")
        assert only is None
        assert prompt == ""

    def test_quoted_prompt_with_spaces(self):
        only, cap, prompt = parse_run_command('/run --only codex "fix the flaky test"')
        assert only == ["codex"]
        assert prompt == "fix the flaky test"

    # --- natural-language path: no ``/run`` prefix required ---

    def test_natural_language_prompt(self):
        """Bare prose is dispatched as-is with no flags set."""
        only, cap, prompt = parse_run_command("write a Python function to average a list")
        assert only is None
        assert cap is None
        assert prompt == "write a Python function to average a list"

    def test_natural_language_preserves_unicode(self):
        only, cap, prompt = parse_run_command("寫一個 Python function 計算 list 平均值")
        assert only is None
        assert cap is None
        assert prompt == "寫一個 Python function 計算 list 平均值"

    def test_natural_language_preserves_inner_slashes(self):
        """A path like ``a/b/c`` in prose should not be mistaken for a flag."""
        only, cap, prompt = parse_run_command("explain the difference between a/b and c/d")
        assert only is None
        assert cap is None
        assert prompt == "explain the difference between a/b and c/d"

    def test_run_prefix_is_case_insensitive(self):
        only, cap, prompt = parse_run_command("/RUN hello")
        assert only is None
        assert prompt == "hello"


class TestRouteCommand:
    def test_known_commands_route_to_their_handlers(self):
        assert route_command("/help").__name__ == "handle_help"
        assert route_command("/start").__name__ == "handle_start"
        assert route_command("/status").__name__ == "handle_status"
        assert route_command("/pools").__name__ == "handle_pools"
        assert route_command("/run hi").__name__ == "handle_run"

    def test_run_routes_to_handle_run(self):
        assert route_command("/run ping") is handle_run

    def test_natural_language_routes_to_handle_run(self):
        """Bare prose dispatches — no ``/run`` prefix required."""
        assert route_command("hello") is handle_run
        assert route_command("寫一個 quicksort") is handle_run

    def test_unknown_slash_command_returns_none(self):
        """Typos like ``/statu`` should NOT silently fan out to backends."""
        assert route_command("/statu") is None
        assert route_command("/unknown") is None
        assert route_command("/RUN--with-typo") is None

    def test_empty_text_returns_none(self):
        assert route_command("") is None
        assert route_command("   ") is None


class TestIncomingMessageModel:
    def test_command_flag(self):
        m = IncomingMessage(update_id=1, chat_id=10, user_id=20, text="/help", is_command=True)
        assert m.is_command is True

    def test_default_user_id_none(self):
        m = IncomingMessage(update_id=2, chat_id=10, text="hi")
        assert m.user_id is None


class TestUnbalancedQuotes:
    def test_natural_language_with_apostrophe(self):
        """``don't`` used to raise ValueError out of shlex.split."""
        only, cap, prompt = parse_run_command("don't break on apostrophes")
        assert (only, cap) == (None, None)
        assert prompt == "don't break on apostrophes"

    def test_run_with_unclosed_quote_falls_back_to_split(self):
        only, cap, prompt = parse_run_command('/run --only codex say "hi')
        assert only == ["codex"]
        assert prompt == 'say "hi'

    def test_route_command_tolerates_unclosed_quote(self):
        assert route_command('what is "x') is handle_run


class TestBotnameSuffix:
    def test_command_with_botname(self, monkeypatch):
        monkeypatch.delenv("BOT_USERNAME", raising=False)
        assert route_command("/start@khavis_bot").__name__ == "handle_start"
        assert route_command("/STATUS@Khavis_Bot extra").__name__ == "handle_status"
        assert route_command("/run@khavis_bot hi") is handle_run

    def test_run_with_botname_parses_prompt(self, monkeypatch):
        monkeypatch.delenv("BOT_USERNAME", raising=False)
        only, _cap, prompt = parse_run_command("/run@khavis_bot --only claude hello")
        assert only == ["claude"]
        assert prompt == "hello"

    def test_other_bots_commands_are_ignored_when_username_known(self, monkeypatch):
        monkeypatch.setenv("BOT_USERNAME", "@khavis_bot")
        assert route_command("/start@khavis_bot").__name__ == "handle_start"
        assert route_command("/start@other_bot") is None


class _FakeAPI:
    def __init__(self):
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text, *, parse_mode=None, reply_to=None):
        self.sent.append((chat_id, text, parse_mode))
        return {"message_id": 1}


class TestStatusHandler:
    def test_status_reads_health_result_attributes(self):
        import asyncio

        from bot.backends.base import HealthResult
        from bot.handlers import HandlerDeps, handle_status

        class _B:
            def __init__(self, h):
                self._h = h

            async def health(self):
                return self._h

        api = _FakeAPI()
        deps = HandlerDeps(
            telegram=api,
            allowlist=[5],
            backends={
                "claude": _B(HealthResult(True, "claude 2.0")),
                "codex": _B(HealthResult(False, "not in PATH")),
                "legacy": _B({"ok": True, "detail": "dict ok"}),
            },
        )
        asyncio.run(handle_status(IncomingMessage(update_id=1, chat_id=5, text="/status"), deps))
        (_chat, text, parse_mode) = api.sent[0]
        assert parse_mode is None
        assert "✅ claude - claude 2.0" in text
        assert "❌ codex - not in PATH" in text
        assert "✅ legacy - dict ok" in text
        assert "error" not in text  # no AttributeError from .get() on a dataclass

    def test_help_text_is_plain(self):
        from bot.handlers import HELP_TEXT

        assert "\\-" not in HELP_TEXT and "\\<" not in HELP_TEXT


# ----------------------------------------------------------------------
# /phase progress must not become a message flood
# ----------------------------------------------------------------------
class _RecordingTelegram:
    """Captures every Telegram call so we can count messages, not edits."""

    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.edited: list[tuple[int, int, str]] = []
        self._next_id = 1000

    async def send_message(self, chat_id, text, *, parse_mode=None, reply_to=None):
        self._next_id += 1
        self.sent.append((chat_id, text))
        return {"message_id": self._next_id}

    async def edit_message(self, chat_id, message_id, text, *, parse_mode=None):
        self.edited.append((chat_id, message_id, text))
        return {"message_id": message_id}


class _Deps:
    def __init__(self, telegram):
        self.telegram = telegram
        self.allowlist = [42]
        self.backends = {}
        self.pools = []
        self.judge = None
        self.secrets = None


def test_phase_progress_does_not_send_one_message_per_event(monkeypatch):
    """A full pipeline fires ~16 progress events; that must be one message
    edited in place, not sixteen messages in the user's chat."""
    import asyncio

    from bot import handlers as H

    telegram = _RecordingTelegram()
    deps = _Deps(telegram)

    async def fake_run_phase(name, prompt, on_progress=None):
        for i in range(16):
            await on_progress(f"step {i}")
        return type(
            "R",
            (),
            {"phase": "pipeline", "ok": True, "latency_ms": 1, "output": "ok", "error": None},
        )()

    monkeypatch.setattr("bot.phases.run_phase", fake_run_phase)
    msg = IncomingMessage(
        update_id=1, message_id=9, chat_id=42, user_id=1, text="/phase pipeline go"
    )
    asyncio.run(H.handle_phase(msg, deps))

    # Exactly one progress message plus the final result message.
    assert len(telegram.sent) == 2, f"sent {len(telegram.sent)} messages: {telegram.sent}"
    assert telegram.sent[0][1] == "step 0"
    assert telegram.sent[1][1].startswith("✅")


def test_phase_progress_spread_over_time_edits_in_place(monkeypatch):
    """Slow phases must update the existing message, not add new ones."""
    import asyncio

    from bot import handlers as H

    telegram = _RecordingTelegram()
    deps = _Deps(telegram)

    async def fake_run_phase(name, prompt, on_progress=None):
        for i in range(3):
            await on_progress(f"phase {i} starting")
            await asyncio.sleep(1.05)  # clear of the 1 s edit throttle
        return type(
            "R",
            (),
            {"phase": "pipeline", "ok": True, "latency_ms": 1, "output": "o", "error": None},
        )()

    monkeypatch.setattr("bot.phases.run_phase", fake_run_phase)
    msg = IncomingMessage(
        update_id=3, message_id=9, chat_id=42, user_id=1, text="/phase pipeline go"
    )
    asyncio.run(H.handle_phase(msg, deps))

    assert len(telegram.sent) == 2, f"sent {len(telegram.sent)} messages: {telegram.sent}"
    # Progress updates became edits against the first message's id.
    first_id = telegram.sent and 1001
    assert telegram.edited, "slow progress should edit the existing message"
    assert all(e[1] == first_id for e in telegram.edited), telegram.edited


def test_phase_progress_updates_are_rate_limited(monkeypatch):
    """Telegram rejects edits faster than ~1/s per chat; rapid events must
    coalesce rather than burn the budget."""
    import asyncio

    from bot import handlers as H

    telegram = _RecordingTelegram()
    deps = _Deps(telegram)

    async def fake_run_phase(name, prompt, on_progress=None):
        for i in range(50):  # all within the same second
            await on_progress(f"burst {i}")
        return type(
            "R",
            (),
            {"phase": "pipeline", "ok": True, "latency_ms": 1, "output": "o", "error": None},
        )()

    monkeypatch.setattr("bot.phases.run_phase", fake_run_phase)
    msg = IncomingMessage(
        update_id=2, message_id=9, chat_id=42, user_id=1, text="/phase pipeline go"
    )
    asyncio.run(H.handle_phase(msg, deps))

    assert len(telegram.sent) == 2
    assert len(telegram.edited) < 50, f"edited {len(telegram.edited)} times without throttling"
