"""Watchdog-based hot reload for YAML configuration files.

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

import threading
from collections.abc import Callable, Iterable
from pathlib import Path

# ``watchdog`` is an optional runtime dependency: tests / static imports
# should still work even if it isn't installed.
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    _HAVE_WATCHDOG = True
except Exception:  # pragma: no cover - import guard
    Observer = None
    FileSystemEventHandler = object
    _HAVE_WATCHDOG = False


DEFAULT_DEBOUNCE_SECONDS = 1.0


class _DebouncedHandler(FileSystemEventHandler):
    """Coalesce bursts of FS events into one debounced callback *per target*.

    Each watched file has its own timer, so editing ``pools.yaml`` and then
    ``capabilities.yaml`` within the debounce window reloads both instead of
    cancelling the first. Callbacks are serialised (one at a time) so two
    reloads never mutate the registry concurrently.
    """

    def __init__(
        self,
        targets: Iterable[Path],
        callback: Callable[[Path], None],
        debounce: float,
    ) -> None:
        self._targets = {Path(p).resolve() for p in targets}
        self._callback = callback
        self._debounce = debounce
        self._timers: dict[Path, threading.Timer] = {}
        self._lock = threading.Lock()
        self._callback_lock = threading.Lock()
        self._stopped = False

    # Watchdog event hooks -------------------------------------------------
    def on_modified(self, event):  # noqa: D401 - watchdog signature
        self._maybe_trigger(event)

    def on_created(self, event):
        self._maybe_trigger(event)

    def on_moved(self, event):
        self._maybe_trigger(event)

    # Internal -------------------------------------------------------------
    def _maybe_trigger(self, event) -> None:
        if event.is_directory:
            return
        path = Path(getattr(event, "dest_path", None) or event.src_path).resolve()
        if not self._targets or path not in self._targets:
            return
        with self._lock:
            if self._stopped:
                return
            previous = self._timers.get(path)
            if previous is not None:
                previous.cancel()
            timer = threading.Timer(self._debounce, self._fire, args=[path])
            timer.daemon = True
            self._timers[path] = timer
            timer.start()

    def pending(self) -> list[Path]:
        """Targets with a scheduled (not yet fired) reload."""
        with self._lock:
            return [p for p, t in self._timers.items() if t.is_alive()]

    def cancel_all(self) -> None:
        """Cancel every pending timer and refuse new ones (used by ``stop``)."""
        with self._lock:
            self._stopped = True
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            timer.cancel()

    def _fire(self, path: Path) -> None:
        with self._lock:
            if self._stopped:
                return
            current = self._timers.get(path)
            if current is threading.current_thread():
                del self._timers[path]
        with self._callback_lock:
            try:
                self._callback(path)
            except Exception as exc:  # pragma: no cover - defensive
                # We never want a bad reload to take down the watcher.
                print(f"[hot_reload] callback error for {path}: {exc!r}")


class HotReloader:
    """Watch a set of YAML files and re-invoke a callback when they change.

    The callback is invoked on a background thread *after* the file has
    been stable for ``debounce`` seconds (debounced per file), and never
    runs concurrently with itself. If ``watchdog`` is not installed,
    :meth:`start` becomes a no-op (useful for offline tests).

    Only processes that explicitly start a ``HotReloader`` get live reload —
    today that is ``scripts/start.sh`` (``pools.yaml`` +
    ``capabilities.yaml``). ``khavis serve`` / ``server.openai_api`` does
    not watch config; restart it after editing YAML.
    """

    def __init__(
        self,
        paths: Iterable[Path],
        callback: Callable[[Path], None],
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self.paths: list[Path] = [Path(p) for p in paths]
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._observer: Observer | None = None
        self._handler: _DebouncedHandler | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if not _HAVE_WATCHDOG:
            print("[hot_reload] watchdog not installed; hot reload disabled.")
            return
        if self._observer is not None:
            return  # already running

        handler = _DebouncedHandler(
            targets=self.paths,
            callback=self.callback,
            debounce=self.debounce_seconds,
        )
        observer = Observer()
        # Watch the parent dirs so creation/rename events are caught too.
        watched_dirs = {p.resolve().parent for p in self.paths}
        for d in watched_dirs:
            observer.schedule(handler, str(d), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer
        self._handler = handler

    def stop(self, timeout: float = 2.0) -> None:
        """Stop watching and cancel any debounced reload that has not fired yet."""
        if self._handler is not None:
            self._handler.cancel_all()
            self._handler = None
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=timeout)
        self._observer = None

    @property
    def running(self) -> bool:
        return self._observer is not None

    def is_available(self) -> bool:
        return _HAVE_WATCHDOG


__all__ = ["HotReloader", "DEFAULT_DEBOUNCE_SECONDS"]
