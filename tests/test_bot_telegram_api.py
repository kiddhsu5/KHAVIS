"""Unit tests for Telegram MarkdownV2 escaping and result formatting."""

from __future__ import annotations

from bot.models import BackendResult
from bot.telegram_api import TelegramAPI


class TestEscapeMarkdownV2:
    def test_escapes_all_specials(self):
        specials = "_*[]()~`>#+-=|{}.!\\"
        for ch in specials:
            assert TelegramAPI.escape_markdown_v2(ch) == f"\\{ch}"

    def test_passes_through_normal_text(self):
        assert TelegramAPI.escape_markdown_v2("hello world") == "hello world"

    def test_handles_empty(self):
        assert TelegramAPI.escape_markdown_v2("") == ""

    def test_escapes_dot(self):
        # Plain "." is special in MarkdownV2 (only inside number-prefixed lines,
        # but escaping everywhere is safe).
        assert TelegramAPI.escape_markdown_v2("a.b") == "a\\.b"


class TestFormatResultBlock:
    def test_success(self):
        r = BackendResult(backend="claude", ok=True, text="hi", model="m", latency_ms=1234)
        block = TelegramAPI.format_result_block(r)
        assert "✅" in block
        assert "claude" in block
        assert "1.2s" in block
        assert "hi" in block

    def test_failure(self):
        r = BackendResult(
            backend="codex", ok=False, error="rate limited", model="m", latency_ms=500
        )
        block = TelegramAPI.format_result_block(r)
        assert "❌" in block
        assert "rate limited" in block

    def test_truncates_long_text(self):
        r = BackendResult(backend="claude", ok=True, text="x" * 5000, model="m")
        block = TelegramAPI.format_result_block(r)
        # Truncated to ~1500 chars + ellipsis
        body_lines = block.splitlines()
        assert any(line.endswith("…") for line in body_lines)
