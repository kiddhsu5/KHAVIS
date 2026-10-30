"""Agent factory: bridges :class:`Role` objects to runnable LLM agents.

The factory consults the existing :class:`core.capability_router.CapabilityRouter`
to pick a pool for each role, then wraps that pool in a thin LangChain-
compatible runnable. Agent instances are cached by ``(role_name, pool_name)``
so repeated invocations don't re-instantiate expensive clients.

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
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure project root is importable regardless of where this module is loaded.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.capability_router import CapabilityRouter  # noqa: E402
from core.registry import PluginRegistry  # noqa: E402
from providers.base import ProviderPlugin  # noqa: E402

from .roles import Role  # noqa: E402

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Thin runnable wrapper around a ProviderPlugin
# ----------------------------------------------------------------------
@dataclass
class _PoolRunnable:
    """A LangChain-style runnable that proxies ``invoke`` to a pool.

    Implements the minimal interface that nodes need: ``invoke(messages)``
    returning a string answer. Streaming is best-effort — we delegate to
    the pool's ``chat`` method which already returns the full reply.
    """

    pool: ProviderPlugin
    role: Role
    fallback_chain: list[str]
    # Captured at construction time so the runnable always sees the
    # factory that built it, even if the module-level singleton is
    # later swapped (e.g. by tests).
    factory: Any | None = None

    def _resolve(self, name: str) -> Any:
        if self.factory is not None:
            return self.factory._resolve(name)
        return get_agent_factory()._resolve(name)

    def invoke(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        # Try the primary pool + every fallback in parallel; first success wins.
        # This makes ``agents.run_team`` dramatically faster when the
        # primary pool is rate-limited or has bad creds — instead of
        # waiting for the primary to time out then sequentially probing
        # each fallback, all pools race. Sequential behaviour was the
        # root cause of multi-minute ``run_team`` calls when only some
        # pools were healthy.
        attempt = [self.pool.name] + list(self.fallback_chain)
        last_err: Exception | None = None

        def _try(name: str) -> tuple[str, str | None, Exception | None]:
            plugin = self._resolve(name) or self.pool
            try:
                response = plugin.chat(messages, **kwargs)
                content = _extract_content(response)
            except Exception as exc:  # pragma: no cover - defensive
                return name, None, exc
            if not content or not content.strip():
                # An empty / null reply is a failure, not a winner: keep
                # waiting for the other pools in the race.
                return name, None, RuntimeError(f"pool {name!r} returned empty content")
            return name, content, None

        # Cap parallelism at the number of attempts so we never spawn more
        # workers than pools. In practice this is a small pool set.
        executor = ThreadPoolExecutor(
            max_workers=max(1, len(attempt)),
            thread_name_prefix=f"agent-{self.role.name}",
        )
        futures = {executor.submit(_try, n): n for n in attempt}
        try:
            for f in as_completed(futures, timeout=None):
                _name, content, err = f.result()
                if content is not None:
                    # First success wins. Cancel pending fallbacks (this
                    # only stops ones that haven't started yet — a slow
                    # primary that's already inside ``chat`` will keep
                    # running until its own timeout / error, but
                    # ``shutdown(wait=False)`` below lets us return
                    # without joining on those threads).
                    for remaining in futures:
                        if remaining is not f:
                            remaining.cancel()
                    log.debug("pool %s answered (role=%s)", _name, self.role.name)
                    return content
                if err is not None:
                    last_err = err
                    log.warning("pool %s failed for role %s: %r", _name, self.role.name, err)
        finally:
            # wait=False so the primary that timed out doesn't block the
            # caller's return. Threads that are already blocked in
            # ``chat()`` will keep running until they time out on their
            # own; we just don't join on them.
            executor.shutdown(wait=False)
            for f in futures:
                if not f.done():
                    f.cancel()

        raise RuntimeError(f"all pools failed for role {self.role.name!r}: {last_err!r}")

    # LangChain compatibility shims.
    def __call__(self, messages: list[dict[str, str]], **kwargs: Any) -> str:  # type: ignore[override]
        return self.invoke(messages, **kwargs)


def _as_text(value: Any) -> str:
    """``None`` -> ``""`` (never the literal string ``"None"``)."""
    return "" if value is None else str(value)


def _extract_content(response: Any) -> str:
    """Normalise a provider response to a plain string.

    Missing / ``null`` content becomes ``""`` so callers can treat it as an
    empty (failed) answer.
    """
    if response is None:
        return ""
    if not isinstance(response, dict):
        return str(response)
    if "choices" in response:
        choices = response.get("choices") or []
        if not choices:
            return ""
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message") or {}
            if isinstance(msg, dict) and "content" in msg:
                return _as_text(msg.get("content"))
            return _as_text(first.get("text"))
        return _as_text(first)
    if "content" in response:
        return _as_text(response["content"])
    return str(response)


# ----------------------------------------------------------------------
# AgentFactory
# ----------------------------------------------------------------------
class AgentFactory:
    """Materialises :class:`Role` -> runnable.

    The factory owns the registry + capability router instances so multiple
    agents share one plugin catalogue.
    """

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        router: CapabilityRouter | None = None,
        config_dir: Path | None = None,
    ) -> None:
        root = Path(config_dir) if config_dir else _PROJECT_ROOT
        self.registry: PluginRegistry = registry or PluginRegistry(root).discover()
        self.router: CapabilityRouter = router or CapabilityRouter(self.registry)
        caps_path = root / "config" / "capabilities.yaml"
        if caps_path.exists():
            self.router.load_capabilities(caps_path)
        self._cache: dict[tuple[str, str], _PoolRunnable] = {}

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def _resolve(self, pool_name: str) -> ProviderPlugin | None:
        return self.registry.get(pool_name)

    def _fallback_for(self, capability: str, exclude: str | None = None) -> list[str]:
        candidates = self.router.candidates(capability)
        names = [p.name for p in candidates]
        if exclude and exclude in names:
            names.remove(exclude)
        return names

    def select_pool(self, role: Role) -> ProviderPlugin | None:
        """Pick a single pool for ``role`` (public for diagnostics)."""
        return self.router.select(role.capability_required)

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    def create_agent(
        self,
        role: Role,
        *,
        pool_name: str | None = None,
    ) -> _PoolRunnable:
        """Return a runnable for ``role``.

        ``pool_name`` overrides the capability router when the caller wants
        to pin a specific pool (e.g. for cross-validation).
        """
        primary = self._resolve(pool_name) if pool_name else self.select_pool(role)
        if primary is None:
            raise RuntimeError(f"no pool available for capability {role.capability_required!r}")

        cache_key = (role.name, primary.name)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        fallbacks = [
            n
            for n in self._fallback_for(role.capability_required, exclude=primary.name)
            if n != primary.name
        ]
        runnable = _PoolRunnable(pool=primary, role=role, fallback_chain=fallbacks, factory=self)
        self._cache[cache_key] = runnable
        log.info(
            "agent created role=%s pool=%s fallbacks=%s",
            role.name,
            primary.name,
            fallbacks,
        )
        return runnable

    def reset_cache(self) -> None:
        """Clear the agent cache (e.g. after hot-reload)."""
        self._cache.clear()


# Singleton accessor for the simple case where there is only one factory
# per process. Node functions can import this lazily.
_AGENT_FACTORY: AgentFactory | None = None


def get_agent_factory() -> AgentFactory:
    global _AGENT_FACTORY
    if _AGENT_FACTORY is None:
        _AGENT_FACTORY = AgentFactory()
    return _AGENT_FACTORY


def set_agent_factory(factory: AgentFactory) -> None:
    """Override the singleton (used by tests)."""
    global _AGENT_FACTORY
    _AGENT_FACTORY = factory


__all__ = [
    "AgentFactory",
    "get_agent_factory",
    "set_agent_factory",
]
