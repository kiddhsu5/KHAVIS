"""Tests for the YAML hot-reload watcher.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from core.hot_reload import (
    DEFAULT_DEBOUNCE_SECONDS,
    HotReloader,
    _DebouncedHandler,
)


# ---------------------------------------------------------------------------
# HotReloader.start without watchdog
# ---------------------------------------------------------------------------
class TestWithoutWatchdog:
    def test_start_is_noop_when_watchdog_missing(self, tmp_path, capsys):
        target = tmp_path / "pools.yaml"
        target.write_text("pools: []\n")
        reloader = HotReloader([target], lambda p: None)
        # Force the "watchdog missing" path even on systems where it is installed.
        with patch("core.hot_reload._HAVE_WATCHDOG", False):
            reloader.start()
        out = capsys.readouterr().out
        assert "hot reload disabled" in out
        assert reloader.running is False

    def test_is_available_reports_watchdog_status(self):
        reloader = HotReloader([], lambda p: None)
        # Just verify the property works without raising.
        assert isinstance(reloader.is_available(), bool)

    def test_stop_when_never_started_is_safe(self):
        reloader = HotReloader([], lambda p: None)
        reloader.stop()  # Should not raise.
        assert reloader.running is False


# ---------------------------------------------------------------------------
# _DebouncedHandler
# ---------------------------------------------------------------------------
class TestDebouncedHandler:
    def _event(self, path: Path, *, is_directory: bool = False) -> MagicMock:
        ev = MagicMock()
        ev.is_directory = is_directory
        ev.src_path = str(path)
        ev.dest_path = None
        return ev

    def test_ignores_directories(self, tmp_path):
        seen: list[Path] = []
        handler = _DebouncedHandler([tmp_path / "x.yaml"], seen.append, 0.05)
        handler._maybe_trigger(self._event(tmp_path, is_directory=True))
        # No timer should be scheduled.
        assert handler._timers == {}

    def test_ignores_unrelated_files(self, tmp_path):
        target = tmp_path / "target.yaml"
        other = tmp_path / "other.yaml"
        target.write_text("a")
        other.write_text("b")

        seen: list[Path] = []
        handler = _DebouncedHandler([target], seen.append, 0.05)
        handler._maybe_trigger(self._event(other))
        assert handler._timers == {}

    def test_fires_callback_after_debounce(self, tmp_path):
        target = tmp_path / "pools.yaml"
        target.write_text("a")
        seen: list[Path] = []
        handler = _DebouncedHandler([target], seen.append, 0.05)
        handler._maybe_trigger(self._event(target))
        # Wait for the timer to fire.
        time.sleep(0.2)
        assert seen and seen[0].resolve() == target.resolve()

    def test_coalesces_bursts(self, tmp_path):
        target = tmp_path / "pools.yaml"
        target.write_text("a")
        seen: list[Path] = []
        handler = _DebouncedHandler([target], seen.append, 0.1)
        # Three rapid triggers → only the last should fire.
        handler._maybe_trigger(self._event(target))
        handler._maybe_trigger(self._event(target))
        handler._maybe_trigger(self._event(target))
        time.sleep(0.25)
        # Same target -> one timer, one callback.
        assert len(seen) == 1

    def test_each_target_has_its_own_debounce(self, tmp_path):
        a = tmp_path / "pools.yaml"
        b = tmp_path / "capabilities.yaml"
        a.write_text("a")
        b.write_text("b")
        seen: list[Path] = []
        handler = _DebouncedHandler([a, b], seen.append, 0.1)
        handler._maybe_trigger(self._event(a))
        handler._maybe_trigger(self._event(b))  # must NOT cancel a's reload
        time.sleep(0.3)
        assert sorted(p.name for p in seen) == ["capabilities.yaml", "pools.yaml"]

    def test_cancel_all_drops_pending_timers(self, tmp_path):
        target = tmp_path / "pools.yaml"
        target.write_text("a")
        seen: list[Path] = []
        handler = _DebouncedHandler([target], seen.append, 0.1)
        handler._maybe_trigger(self._event(target))
        assert handler.pending()
        handler.cancel_all()
        time.sleep(0.2)
        assert seen == []
        # And no new timers after cancel.
        handler._maybe_trigger(self._event(target))
        assert handler._timers == {}

    def test_callbacks_are_serialised(self, tmp_path):
        import threading

        a = tmp_path / "pools.yaml"
        b = tmp_path / "capabilities.yaml"
        a.write_text("a")
        b.write_text("b")
        active = 0
        peak = 0
        lock = threading.Lock()

        def slow(_path: Path) -> None:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.1)
            with lock:
                active -= 1

        handler = _DebouncedHandler([a, b], slow, 0.01)
        handler._maybe_trigger(self._event(a))
        handler._maybe_trigger(self._event(b))
        time.sleep(0.4)
        assert peak == 1

    def test_callback_exceptions_are_swallowed(self, tmp_path, capsys):
        target = tmp_path / "pools.yaml"
        target.write_text("a")
        handler = _DebouncedHandler(
            [target], lambda p: (_ for _ in ()).throw(RuntimeError("boom")), 0.05
        )
        # Should not raise.
        handler._fire(target)
        out = capsys.readouterr().out
        assert "callback error" in out


# ---------------------------------------------------------------------------
# HotReloader with a stub Observer
# ---------------------------------------------------------------------------
class TestWithStubObserver:
    def _setup_stub_observer(self, monkeypatch):
        """Replace watchdog Observer/Handler with a stub."""
        from core import hot_reload

        class StubObserver:
            def __init__(self):
                self.scheduled: list[Any] = []
                self.started = False
                self.stopped = False

            def schedule(self, handler, path, recursive=False):
                self.scheduled.append((handler, path, recursive))

            def start(self):
                self.started = True

            def stop(self):
                self.stopped = True

            def join(self, timeout=None):
                pass

        monkeypatch.setattr(hot_reload, "_HAVE_WATCHDOG", True)
        monkeypatch.setattr(hot_reload, "Observer", StubObserver)

    def test_start_uses_observer(self, tmp_path, monkeypatch):
        self._setup_stub_observer(monkeypatch)
        target = tmp_path / "pools.yaml"
        target.write_text("a")
        reloader = HotReloader([target], lambda p: None, debounce_seconds=0.05)
        reloader.start()
        assert reloader.running is True

        reloader.stop()
        assert reloader.running is False

    def test_start_is_idempotent(self, tmp_path, monkeypatch):
        self._setup_stub_observer(monkeypatch)
        target = tmp_path / "pools.yaml"
        target.write_text("a")
        reloader = HotReloader([target], lambda p: None)
        reloader.start()
        reloader.start()
        # Should only have one observer internally.
        assert reloader.running is True
        reloader.stop()

    def test_yaml_change_triggers_callback(self, tmp_path, monkeypatch):
        self._setup_stub_observer(monkeypatch)
        target = tmp_path / "pools.yaml"
        target.write_text("a: 1\n")
        seen: list[Path] = []
        reloader = HotReloader([target], seen.append, debounce_seconds=0.05)
        reloader.start()
        try:
            # Simulate a watchdog event directly through the handler.
            handler = reloader._observer.scheduled[0][0]
            ev = MagicMock()
            ev.is_directory = False
            ev.src_path = str(target)
            ev.dest_path = None
            handler._maybe_trigger(ev)
            time.sleep(0.2)
        finally:
            reloader.stop()
        assert seen, "callback should have fired after debounce"

    def test_stop_cancels_pending_reload(self, tmp_path, monkeypatch):
        self._setup_stub_observer(monkeypatch)
        target = tmp_path / "pools.yaml"
        target.write_text("a: 1\n")
        seen: list[Path] = []
        reloader = HotReloader([target], seen.append, debounce_seconds=0.1)
        reloader.start()
        handler = reloader._observer.scheduled[0][0]
        ev = MagicMock()
        ev.is_directory = False
        ev.src_path = str(target)
        ev.dest_path = None
        handler._maybe_trigger(ev)
        reloader.stop()
        time.sleep(0.25)
        assert seen == [], "stop() must cancel the debounced reload"


# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------
def test_default_debounce_is_positive():
    assert DEFAULT_DEBOUNCE_SECONDS > 0
