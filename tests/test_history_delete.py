"""History delete rewrites the jsonl and leaves workspace files alone."""

from __future__ import annotations

import pytest

from bot import history as bot_history


def test_delete_one_event_keeps_the_rest(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KHAVIS_LOG_DIR", str(tmp_path))
    bot_history.record({"prompt": "keep me", "ts": 10.0, "ok": True})
    bot_history.record({"prompt": "drop me", "ts": 20.0, "ok": False})

    result = bot_history.delete_events([{"ts": 20.0, "prompt": "drop me"}])

    assert result["removed"] == 1
    assert result["kept"] == 1
    assert [row["prompt"] for row in bot_history.tail(10)] == ["keep me"]


def test_same_timestamp_without_prompt_is_not_deleted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KHAVIS_LOG_DIR", str(tmp_path))
    bot_history.record({"prompt": "first", "ts": 5.0})
    bot_history.record({"prompt": "second", "ts": 5.0})

    result = bot_history.delete_events([{"ts": 5.0}])

    assert result["removed"] == 0
    assert result["ambiguous"] == 1
    assert len(bot_history.tail(10)) == 2


def test_clear_removes_every_line(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KHAVIS_LOG_DIR", str(tmp_path))
    bot_history.record({"prompt": "a", "ts": 1.0})
    bot_history.record({"prompt": "b", "ts": 2.0})

    result = bot_history.delete_events(clear=True)

    assert result["removed"] == 2
    assert bot_history.tail(10) == []


def test_delete_requires_a_target() -> None:
    with pytest.raises(ValueError):
        bot_history.delete_events([])
