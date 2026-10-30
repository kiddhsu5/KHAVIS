"""Capability-based router.

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

import itertools
import random
import threading
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from providers.base import ProviderPlugin

from .registry import PluginRegistry


@dataclass
class CapabilityEntry:
    """One YAML entry mapping a capability tag to candidate pools + weights.

    ``pools`` accepts three shapes so existing configs keep working:

    * list form  — ``pools: [A, B]`` plus an entry-wide ``weight``
    * map form   — ``pools: {A: 1.2, B: 0.85}`` for per-pool weighting
    * mixed list — ``pools: [A, {B: 0.85}]``: plain names take the
      entry-wide ``weight``, single-key maps override it for that pool

    The map / mixed forms are what make the documented "down-weight BYOK
    pools" policy expressible; the list form applies one weight to every
    listed pool. Order is preserved in every form (``strategy="priority"``
    depends on it).
    """

    capability: str
    pools: list[str] = field(default_factory=list)
    weight: float = 1.0
    pool_weights: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityEntry:
        raw_pools = data.get("pools", [])
        pool_weights: dict[str, float] = {}
        pools: list[str] = []
        if isinstance(raw_pools, dict):
            pool_weights = {str(k): float(v) for k, v in raw_pools.items()}
            pools = list(pool_weights)
        else:
            for item in raw_pools or []:
                if isinstance(item, dict):
                    for k, v in item.items():
                        pools.append(str(k))
                        pool_weights[str(k)] = float(v)
                else:
                    pools.append(str(item))
        return cls(
            capability=data["capability"],
            pools=pools,
            weight=float(data.get("weight", 1.0)),
            pool_weights=pool_weights,
        )

    def weight_for(self, pool_name: str) -> float:
        """Weight of ``pool_name`` under this entry."""
        if pool_name in self.pool_weights:
            return self.pool_weights[pool_name]
        if pool_name in self.pools:
            return self.weight
        return 1.0


# Round-robin ticket key for :meth:`CapabilityRouter.select_any`, which has no
# capability to key on. Kept distinct from any real capability tag.
_ANY_POOL_KEY = "\x00any"


class CapabilityRouter:
    """Routes requests by capability tag, not by hard-coded pool name."""

    def __init__(self, registry: PluginRegistry) -> None:
        self.registry = registry
        self._entries: list[CapabilityEntry] = []
        # One monotonically increasing counter per capability for
        # ``strategy="round_robin"``; guarded so concurrent callers each get
        # a distinct ticket.
        self._rr_counters: dict[str, itertools.count[int]] = {}
        self._rr_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def load_capabilities(self, path: Path) -> CapabilityRouter:
        """Load capability → pools mappings from a YAML file."""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        entries_raw = data.get("capabilities", [])
        self._entries = [CapabilityEntry.from_dict(item) for item in entries_raw]
        return self

    def entries(self) -> list[CapabilityEntry]:
        return list(self._entries)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    def candidates(self, capability: str) -> list[ProviderPlugin]:
        """Return registry plugins that advertise ``capability``, in config order.

        The YAML ``pools`` order is significant: it is the operator's declared
        preference, and ``strategy="priority"`` depends on it.
        """
        # First, look for an explicit YAML entry; otherwise fall back to the
        # registry's own capability tags so the system degrades gracefully
        # when the YAML is incomplete.
        explicit = next((e for e in self._entries if e.capability == capability), None)
        plugins: list[ProviderPlugin] = []
        if explicit and explicit.pools:
            for name in explicit.pools:
                plugin = self.registry.get(name)
                if plugin is not None:
                    plugins.append(plugin)
        if not plugins:
            plugins = self.registry.by_capability(capability)
        return plugins

    def _apply_health_filter(
        self,
        candidates: list[ProviderPlugin],
        exclude: Collection[str],
    ) -> tuple[list[ProviderPlugin], bool]:
        """Drop pools whose circuit is open, whose quota is spent, or excluded.

        Returns ``(kept, needs_claim)``. ``needs_claim`` is ``True`` when the
        caller must claim the chosen pool via ``tracker.is_available`` (which
        hands out the single half-open probe); it is ``False`` when filtering
        is disabled or the policy degraded to the unfiltered list.
        """
        from .health import get_tracker  # noqa: PLC0415 - avoid import cycle at module load

        tracker = get_tracker()
        skip = set(exclude)
        if not tracker.policy.filter_router:
            return [p for p in candidates if p.name not in skip], False

        by_name = {p.name: p for p in candidates}
        names = [p.name for p in candidates]
        kept = tracker.filter_available(names, exclude=skip, degrade=False)
        if kept:
            return [by_name[n] for n in kept], True
        degraded = tracker.filter_available(names, exclude=skip)
        return [by_name[n] for n in degraded], False

    def _next_ticket(self, capability: str) -> int:
        with self._rr_lock:
            counter = self._rr_counters.get(capability)
            if counter is None:
                counter = self._rr_counters[capability] = itertools.count()
            return next(counter)

    def _pick(
        self,
        capability: str,
        candidates: list[ProviderPlugin],
        strategy: str,
        rng: random.Random | Any,
    ) -> ProviderPlugin:
        if strategy == "random":
            return rng.choice(candidates)  # type: ignore[no-any-return]
        if strategy == "priority":
            return candidates[0]
        if strategy == "round_robin":
            return candidates[self._next_ticket(capability) % len(candidates)]
        # Default: weighted random using the YAML weight when present.
        explicit = next((e for e in self._entries if e.capability == capability), None)
        weights: list[float] = []
        for plugin in candidates:
            if explicit:
                # Per-pool weight if the entry declares one, else the
                # entry-wide weight, else a neutral 1.0.
                weights.append(explicit.weight_for(plugin.name))
            else:
                weights.append(1.0)
        return _weighted_choice(candidates, weights, rng)  # type: ignore[no-any-return]

    def select(
        self,
        capability: str,
        *,
        strategy: str = "weighted",
        rng: random.Random | None = None,
        session_id: str | None = None,
        exclude: Collection[str] = (),
    ) -> ProviderPlugin | None:
        """Pick a single plugin for the given capability.

        ``strategy`` can be ``"weighted"`` (default), ``"priority"``,
        ``"round_robin"`` or ``"random"``. ``"priority"`` walks the YAML
        ``pools`` order and takes the first available pool, which is the
        deterministic behaviour a failover path wants. ``"round_robin"``
        rotates through the available candidates with one thread-safe
        counter per capability. Under ``"weighted"`` a pool with weight
        ``0`` is never chosen (unless every candidate weighs 0).

        ``session_id`` applies the token budget cap — callers must pass the
        same session id they later use for ``BudgetGuard.reserve``/``record``
        so routing and enforcement agree. ``exclude`` lets a retry loop tell
        the router which pools already failed this attempt.

        Returns ``None`` when no candidate is available — callers should treat
        that as "every pool is down or spent" and surface it, not silently
        re-route into a known-bad pool (unless the health policy says
        ``on_all_unavailable: degrade``).
        """
        candidates = self.candidates(capability)
        candidates, needs_claim = self._apply_health_filter(candidates, exclude)

        if session_id:
            from .budget import get_guard  # noqa: PLC0415 - avoid import cycle at module load

            guard = get_guard()
            if guard.policy.filter_router:
                kept = [
                    p
                    for p in candidates
                    if guard.check(session_id=session_id, pool=p.name, prompt_chars=0)[0]
                ]
                # A budget cap must never turn into an outage: if every pool is
                # over budget, fall back to the health-filtered set and let the
                # guard itself reject the call with a clear reason.
                candidates = kept or candidates

        chooser = rng or random
        if not needs_claim:
            return self._pick(capability, candidates, strategy, chooser) if candidates else None

        # Claim the chosen pool: listing candidates never hands out a
        # half-open probe, only the pool actually returned does. If another
        # caller won the probe in the meantime, drop it and pick again.
        from .health import get_tracker  # noqa: PLC0415

        tracker = get_tracker()
        remaining = list(candidates)
        while remaining:
            plugin = self._pick(capability, remaining, strategy, chooser)
            if tracker.is_available(plugin.name):
                return plugin
            remaining = [p for p in remaining if p is not plugin]
        return None

    def select_any(
        self,
        *,
        strategy: str = "priority",
        rng: random.Random | None = None,
        exclude: Collection[str] = (),
    ) -> ProviderPlugin | None:
        """Pick any healthy pool, ignoring capability tags.

        This is the failover target for a *pinned* request. When the caller
        names a model explicitly, the pinned pool is the only one guaranteed
        to serve that exact model — but returning the caller's 429 is exactly
        what :func:`server.openai_api._call_with_failover` exists to prevent.
        So we keep the request alive by handing it to another healthy pool,
        which means the response may come from a different model. Callers that
        must stay on the pinned model opt out with ``KHAVIS_FAILOVER=0``.

        Shares the health filtering and the single-half-open-probe claim with
        :meth:`select`, so it cannot hand out a probe that is already spent.
        """
        candidates, needs_claim = self._apply_health_filter(list(self.registry.all()), exclude)
        chooser = rng or random
        if not needs_claim:
            return self._pick(_ANY_POOL_KEY, candidates, strategy, chooser) if candidates else None

        from .health import get_tracker  # noqa: PLC0415

        tracker = get_tracker()
        remaining = list(candidates)
        while remaining:
            plugin = self._pick(_ANY_POOL_KEY, remaining, strategy, chooser)
            if tracker.is_available(plugin.name):
                return plugin
            remaining = [p for p in remaining if p is not plugin]
        return None

    def explain_unavailable(self, capability: str) -> str:
        """Why every candidate for ``capability`` was rejected. For error bodies."""
        from .health import get_tracker  # noqa: PLC0415

        tracker = get_tracker()
        candidates = self.candidates(capability)
        if not candidates:
            return f"no pool advertises capability {capability!r}"
        return "; ".join(tracker.explain(p.name) for p in candidates)


def _weighted_choice(items: list[Any], weights: Iterable[float], rng: random.Random | Any) -> Any:
    """Weighted random pick. Items with weight ``<= 0`` are never chosen.

    When *every* weight is ``<= 0`` the caller expressed no preference at
    all and the pick falls back to uniform.
    """
    items = list(items)
    weights = [max(0.0, float(w)) for w in weights]
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)
    pick = rng.uniform(0, total)
    upto = 0.0
    last_positive = None
    for item, weight in zip(items, weights, strict=False):
        if weight <= 0:
            continue
        last_positive = item
        upto += weight
        if pick < upto:
            return item
    # Float rounding (pick == total): return the last positively weighted item.
    return last_positive


__all__ = ["CapabilityEntry", "CapabilityRouter"]
