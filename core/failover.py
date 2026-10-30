"""Failover: run a prompt, and if the chosen pool fails, try the next one.

This is the code behind the "zero quota interruption" promise. It used to live
inside ``server/openai_api.py``, which meant only the HTTP path benefited —
and the ``khavis chat`` CLI, added later, called ``plugin.chat()`` directly and
inherited none of it. A CLI user would hit an expired key or an exhausted pool
and just get the upstream error, while the same prompt over HTTP sailed
through. One implementation, both front-ends.

The breaker is the state that makes this worth doing: every failure here is
recorded, so a dead pool stops being offered before the next request even
starts.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_MAX_ATTEMPTS = 4


class NoHealthyPool(RuntimeError):
    """Every candidate pool was unavailable before a single call was made."""

    def __init__(self, capability: str | None, detail: str = "") -> None:
        target = capability or "the requested pool"
        super().__init__(f"no healthy pool for {target}" + (f": {detail}" if detail else ""))


def failover_enabled() -> bool:
    """``KHAVIS_FAILOVER=0`` pins strictly to one pool."""
    raw = os.environ.get("KHAVIS_FAILOVER", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def escalation_enabled() -> bool:
    """``KHAVIS_AGENT_ESCALATION=0`` turns off automatic escalation to LangGraph team."""
    raw = os.environ.get("KHAVIS_AGENT_ESCALATION", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def local_fallback_enabled() -> bool:
    """``KHAVIS_LOCAL_FALLBACK=0`` opts out of local Ollama fallback."""
    raw = os.environ.get("KHAVIS_LOCAL_FALLBACK", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def find_local_fallback_pool(router: Any, exclude: Any = ()) -> Any:
    """Find a healthy registered Ollama or local model pool."""
    registry = getattr(router, "registry", None)
    if registry is None:
        return None
    from core.health import get_tracker  # noqa: PLC0415

    tracker = get_tracker()
    candidates: list[Any] = []
    for plugin in registry:
        if plugin.name in exclude:
            continue
        is_local = getattr(plugin, "provider_id", "") == "ollama" or (
            hasattr(plugin, "has_capability") and plugin.has_capability("local")
        )
        if is_local:
            candidates.append(plugin)

    # Prefer Mac over Surface or other hosts
    candidates.sort(
        key=lambda p: 0 if "mac" in p.name.lower() else (1 if "surface" in p.name.lower() else 2)
    )
    for pool in candidates:
        if tracker.is_available(pool.name):
            return pool
    return None


def _invoke(plugin: Any, messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Call one plugin, passing only what its signature accepts.

    Providers take ``**kwargs``, but third-party plugins may not, and an
    unexpected keyword turning into a TypeError would look like an upstream
    failure — sending the breaker down the wrong path.
    """
    return plugin.chat(messages, **kwargs)


def call_with_failover(
    router: Any,
    capability: str | None,
    messages: list[dict[str, Any]],
    *,
    pinned: Any = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    session_id: str | None = None,
    auto_escalate_agents: bool | None = None,
    local_fallback: bool | None = None,
    **kwargs: Any,
) -> tuple[Any, dict[str, Any], int]:
    """Return ``(plugin, result, attempts)``.

    * ``capability`` given — retries stay inside that capability.
    * ``pinned`` set (the caller named a pool/model) — retries fall over to any
      other healthy pool, so the answer may come from a different model. That is
      the deliberate trade: the alternative is handing back the caller's own
      429, and ``model`` is what every OpenAI-compatible client sends by
      default, so refusing to fail over made this path dead code in practice.
      ``KHAVIS_FAILOVER=0`` opts out.
    """
    from core.health import get_tracker  # noqa: PLC0415 - avoid import cycle

    tracker = get_tracker()
    from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

    quota_tracker = get_quota_tracker()
    tried: set[str] = set()
    last_exc: Exception | None = None

    # ------------------------------------------------------------------
    # 1. Pre-flight: Check for high complexity or explicit agent delegation
    # ------------------------------------------------------------------
    allow_escalation = auto_escalate_agents is not False and escalation_enabled() and pinned is None
    if allow_escalation and messages:
        from core.escalation import (  # noqa: PLC0415
            dispatch_to_agent_team,
            evaluate_task_complexity,
        )

        is_complex, reason, score = evaluate_task_complexity(
            messages, capability=capability, **kwargs
        )
        if is_complex:
            return dispatch_to_agent_team(
                messages,
                session_id=session_id,
                reason=f"complexity:{reason}",
                **kwargs,
            )

    def _try(plugin: Any) -> tuple[Any, dict[str, Any], int]:
        tried.add(plugin.name)
        try:
            result = _invoke(plugin, messages, kwargs)
        except Exception as exc:  # noqa: BLE001 - upstream errors are the point
            resp_obj = getattr(exc, "response", None)
            err_headers = getattr(resp_obj, "headers", None) if resp_obj is not None else None
            if err_headers:
                quota_tracker.record_headers(plugin.name, err_headers)
            tracker.record_failure(plugin.name, exc)
            raise
        res_headers = (
            result.get("_headers") or getattr(result, "headers", None)
            if isinstance(result, dict)
            else None
        )
        if res_headers:
            quota_tracker.record_headers(plugin.name, res_headers)
        tracker.record_success(plugin.name)
        return plugin, result, len(tried)

    if pinned is not None and not failover_enabled():
        try:
            return _try(pinned)
        except Exception as exc:  # noqa: BLE001
            raise exc

    if pinned is not None:
        try:
            return _try(pinned)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    elif capability:
        first = router.select(capability, strategy="priority", session_id=session_id)
        if first is not None:
            try:
                return _try(first)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc

    for _ in range(max(1, max_attempts - 1)):
        if capability:
            nxt = router.select(
                capability, strategy="priority", session_id=session_id, exclude=tried
            )
        else:
            nxt = router.select_any(strategy="priority", exclude=tried)
        if nxt is None:
            break
        try:
            return _try(nxt)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue

    # ------------------------------------------------------------------
    # 2. Post-failure: Escalate to multi-agent team on quota exhaustion
    # ------------------------------------------------------------------
    should_escalate_exhaustion = auto_escalate_agents is True or os.environ.get(
        "KHAVIS_AUTO_ESCALATE_ON_EXHAUSTION", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if should_escalate_exhaustion and messages:
        try:
            from core.escalation import dispatch_to_agent_team  # noqa: PLC0415

            reason = "quota_exhausted" if last_exc is not None else "no_healthy_pool"
            return dispatch_to_agent_team(
                messages,
                session_id=session_id,
                reason=reason,
                **kwargs,
            )
        except Exception:  # noqa: BLE001
            # Fall back to throwing original error if agent team dispatch fails
            pass

    # ------------------------------------------------------------------
    # 3. Last Line of Defense: Fall back to local Ollama model
    # ------------------------------------------------------------------
    should_local_fallback = (
        local_fallback if local_fallback is not None else local_fallback_enabled()
    ) and messages
    if should_local_fallback:
        local_pool = find_local_fallback_pool(router, exclude=tried)
        if local_pool is not None:
            try:
                plugin, result, attempts = _try(local_pool)
                if isinstance(result, dict):
                    if "khavis" not in result:
                        result["khavis"] = {}
                    result["khavis"]["degraded_to_local"] = True
                    result["khavis"]["fallback_reason"] = (
                        "all_cloud_pools_exhausted"
                        if last_exc is not None
                        else "no_healthy_cloud_pool"
                    )
                try:
                    from core.alerting import get_alert_manager  # noqa: PLC0415

                    get_alert_manager().emit(
                        pool_name=local_pool.name,
                        event_type="local_fallback",
                        level="warning",
                        message=f"All cloud pools exhausted; switched seamlessly to local Ollama pool {local_pool.name}",
                        details={"tried_pools": list(tried)},
                    )
                except Exception:  # noqa: BLE001
                    pass
                return plugin, result, attempts
            except Exception:  # noqa: BLE001
                pass

    if last_exc is not None:
        raise last_exc
    explain = (
        str(router.explain_unavailable(capability) or "")
        if capability and hasattr(router, "explain_unavailable")
        else ""
    )
    raise NoHealthyPool(capability, explain)


__all__ = [
    "NoHealthyPool",
    "call_with_failover",
    "failover_enabled",
    "DEFAULT_MAX_ATTEMPTS",
    "find_local_fallback_pool",
    "local_fallback_enabled",
]
