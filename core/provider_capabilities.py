"""Discover model capabilities from connected provider metadata endpoints.

The Workbench must not assume that ``high``/``xhigh``/``max`` are valid for
all providers.  This module only exposes thinking/reasoning modes that are
returned by the provider's model catalogue.  It is deliberately stdlib-only
so the status server can use it without adding another runtime dependency.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

_MODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,31}$")
_MODE_KEYS = {
    "thinking_modes",
    "thinking_levels",
    "thinking_options",
    "reasoning_modes",
    "reasoning_levels",
    "reasoning_efforts",
    "supported_reasoning_efforts",
    "effort_levels",
    "effort_options",
}
_PARAMETER_KEYS = {
    "thinking",
    "thinking_mode",
    "thinking_level",
    "reasoning_effort",
    "reasoning_mode",
    "effort",
}
_MODEL_REF_SEPARATOR = "::"
_ENV_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,63}$")


def format_model_ref(provider_env: str | None, model_id: str) -> str:
    """Return a stable model reference that keeps provider identity explicit."""
    provider = str(provider_env or "").strip().upper()
    model = _model_id(model_id)
    if provider and _ENV_REF_RE.fullmatch(provider) and model:
        return f"{provider}{_MODEL_REF_SEPARATOR}{model}"
    return model


def parse_model_ref(value: str | None) -> tuple[str | None, str]:
    """Split ``PROVIDER_ENV::model-id`` while preserving legacy bare IDs."""
    raw = str(value or "").strip()
    if _MODEL_REF_SEPARATOR not in raw:
        return None, _model_id(raw)
    provider, model = raw.split(_MODEL_REF_SEPARATOR, 1)
    provider = provider.strip().upper()
    model = _model_id(model)
    if not provider or not _ENV_REF_RE.fullmatch(provider) or not model:
        return None, _model_id(raw)
    return provider, model


def model_ref_label(provider_label: str | None, model_id: str) -> str:
    """Make duplicate upstream model IDs readable in the Workbench UI."""
    model = _model_id(model_id)
    label = str(provider_label or "自動路由").strip() or "自動路由"
    return f"{label} · {model}"


def _normalise_mode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    mode = value.strip()
    if not mode or mode.lower() in {"true", "false", "supported", "enabled", "disabled"}:
        return None
    return mode if _MODE_RE.fullmatch(mode) else None


def _normalise_parameter(key: str) -> str:
    lowered = key.strip().lower().replace("-", "_")
    if lowered in {"thinking_modes", "thinking_levels", "thinking_options"}:
        return "thinking"
    if lowered in {
        "reasoning_modes",
        "reasoning_levels",
        "reasoning_efforts",
        "supported_reasoning_efforts",
    }:
        return "reasoning_effort"
    if lowered in {"effort_levels", "effort_options"}:
        return "effort"
    return lowered


def _mode_metadata(node: object) -> tuple[list[str], str, bool]:
    """Extract modes, request parameter name and support flag from one object."""
    if not isinstance(node, dict):
        return [], "reasoning_effort", False

    modes: list[str] = []
    parameter = "reasoning_effort"
    supported = False

    for raw_key, value in node.items():
        key = str(raw_key).lower().replace("-", "_")
        if key in _MODE_KEYS:
            supported = True
            parameter = _normalise_parameter(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                mode = _normalise_mode(item)
                if mode and mode not in modes:
                    modes.append(mode)
        elif key in _PARAMETER_KEYS:
            supported = True
            parameter = _normalise_parameter(key)
            if isinstance(value, dict):
                nested_values = value.get("values") or value.get("modes") or value.get("levels")
                if isinstance(nested_values, list):
                    for item in nested_values:
                        mode = _normalise_mode(item)
                        if mode and mode not in modes:
                            modes.append(mode)
            elif isinstance(value, list):
                for item in value:
                    mode = _normalise_mode(item)
                    if mode and mode not in modes:
                        modes.append(mode)
        elif key == "supported_parameters" and isinstance(value, list):
            for item in value:
                marker = str(item).lower().replace("-", "_")
                if "reasoning_effort" in marker or "thinking" in marker or marker == "effort":
                    supported = True
                    parameter = _normalise_parameter(marker)

    # Provider catalogues often nest the actual capability object under
    # ``capabilities`` or ``metadata`` rather than putting it at the model root.
    for child_key in ("capabilities", "metadata", "thinking_config", "reasoning"):
        child = node.get(child_key)
        child_modes, child_parameter, child_supported = _mode_metadata(child)
        if child_modes:
            for mode in child_modes:
                if mode not in modes:
                    modes.append(mode)
        if child_supported:
            supported = True
            parameter = child_parameter

    return modes, parameter, supported


def _model_id(entry: object) -> str:
    if isinstance(entry, str):
        return entry.removeprefix("models/")
    if not isinstance(entry, dict):
        return ""
    raw = entry.get("id") or entry.get("name") or entry.get("model") or entry.get("model_id")
    return str(raw or "").removeprefix("models/").strip()


def _extract_model_entries(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "models", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [{"id": k, **(v if isinstance(v, dict) else {})} for k, v in value.items()]
    return []


def _models_endpoint(base_url: str, env_name: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        return ""
    lower = base.lower()
    if "generativelanguage.googleapis.com" in lower:
        return base if "/models" in lower else f"{base}/v1beta/models"
    if lower.endswith("/chat/completions") or lower.endswith("/responses"):
        base = base.rsplit("/", 1)[0]
        lower = base.lower()
    if "anthropic" in lower or env_name == "MINIMAX_API_KEY":
        return base if lower.endswith("/models") else f"{base}/v1/models"
    return base if lower.endswith("/models") else f"{base}/models"


def _fetch_provider(
    spec: dict[str, Any], values: dict[str, str], timeout_s: float
) -> dict[str, Any]:
    env_name = str(spec.get("env") or "").strip()
    key = values.get(env_name, "").strip()
    if not key and env_name == "ZHIPUAI_API_KEY":
        key = values.get("GLM_API_KEY", "").strip()
    if not key and env_name == "ANTIGRAVITY_API_KEY":
        key = values.get("GOOGLE_API_KEY", "").strip()
    result: dict[str, Any] = {
        "env": env_name,
        "label": str(spec.get("label") or env_name),
        "configured": bool(key),
        "reachable": False,
        "source": "provider:/models",
        "models": [],
    }
    if not key:
        result["status"] = "not_configured"
        return result

    base_env = str(spec.get("base_url_env") or "").strip()
    base_url = values.get(base_env, "").strip() if base_env else ""
    base_url = base_url or str(spec.get("default_base_url") or "").strip()
    endpoint = _models_endpoint(base_url, env_name)
    if not endpoint:
        result["status"] = "no_models_endpoint"
        return result

    if "generativelanguage.googleapis.com" in endpoint.lower():
        separator = "&" if "?" in endpoint else "?"
        endpoint = f"{endpoint}{separator}key={urllib.parse.quote(key, safe='')}"

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {key}",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    req = urllib.request.Request(endpoint, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        result["status"] = f"http_{exc.code}"
        return result
    except (OSError, ValueError):
        result["status"] = "unreachable"
        return result

    models: list[dict[str, Any]] = []
    for entry in _extract_model_entries(payload):
        model_name = _model_id(entry)
        if not model_name:
            continue
        modes, parameter, supported = _mode_metadata(entry)
        models.append(
            {
                "id": model_name,
                "ref": format_model_ref(env_name, model_name),
                "provider_env": env_name,
                "provider_label": result["label"],
                "thinking_modes": modes,
                "thinking_parameter": parameter,
                "thinking_supported": supported,
            }
        )
    result["reachable"] = True
    result["status"] = "ok"
    result["models"] = models
    result["fetched_at"] = datetime.now(UTC).isoformat()
    return result


def discover_provider_capabilities(
    specs: Iterable[dict[str, Any]],
    values: dict[str, str] | None = None,
    *,
    timeout_s: float = 5.0,
) -> list[dict[str, Any]]:
    """Fetch connected provider catalogues concurrently without exposing keys."""
    merged = dict(os.environ)
    if values:
        merged.update({str(k): str(v) for k, v in values.items()})
    unique: dict[str, dict[str, Any]] = {}
    for spec in specs:
        env_name = str(spec.get("env") or "").strip()
        if env_name:
            unique[env_name] = dict(spec)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, len(unique)))) as pool:
        futures = [
            pool.submit(_fetch_provider, spec, merged, timeout_s) for spec in unique.values()
        ]
        return [future.result() for future in futures]


def build_agent_capabilities(
    agent_models: dict[str, str],
    provider_results: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Map each active Agent model to the matching provider metadata."""
    providers = list(provider_results)
    output: dict[str, dict[str, Any]] = {}
    for agent_id, active_model in agent_models.items():
        active_ref = str(active_model or "").strip()
        requested_provider, model_name = parse_model_ref(active_ref)
        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for provider in providers:
            for model in provider.get("models") or []:
                if str(model.get("id") or "") != model_name:
                    continue
                provider_env = str(model.get("provider_env") or provider.get("env") or "").upper()
                if requested_provider and provider_env != requested_provider:
                    continue
                matches.append((provider, model))
        matched_provider: dict[str, Any] | None = None
        matched_model: dict[str, Any] | None = None
        if len(matches) == 1 or requested_provider:
            matched_provider, matched_model = matches[0] if matches else (None, None)
        resolved_ref = active_ref
        if matched_model:
            resolved_ref = str(
                matched_model.get("ref")
                or format_model_ref(matched_model.get("provider_env"), model_name)
            )
        output[agent_id] = {
            "model": model_name,
            "model_ref": resolved_ref,
            "model_display": model_ref_label(
                matched_model.get("provider_label") if matched_model else None,
                model_name,
            ),
            "provider_env": matched_provider.get("env") if matched_provider else None,
            "provider_label": matched_provider.get("label") if matched_provider else None,
            "provider_status": matched_provider.get("status") if matched_provider else "unknown",
            "thinking_modes": list(matched_model.get("thinking_modes") or [])
            if matched_model
            else [],
            "thinking_parameter": (
                matched_model.get("thinking_parameter") if matched_model else "reasoning_effort"
            )
            or "reasoning_effort",
            "thinking_supported": bool(matched_model and matched_model.get("thinking_supported")),
            "source": matched_provider.get("source") if matched_provider else "provider:/models",
        }
    return output


def build_model_catalog(
    agent_models: dict[str, str],
    provider_results: Iterable[dict[str, Any]],
    fallback_options: dict[str, list[str]] | None = None,
    agent_provider_envs: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Build provider-qualified model options without inventing unavailable IDs."""
    providers = list(provider_results)
    catalog: dict[str, dict[str, Any]] = {}
    dynamic_options: dict[str, list[str]] = {agent_id: [] for agent_id in agent_models}

    for provider in providers:
        if provider.get("status") != "ok":
            continue
        provider_env = str(provider.get("env") or "").upper()
        provider_label = str(provider.get("label") or provider_env)
        for model in provider.get("models") or []:
            model_id = _model_id(model.get("id"))
            if not model_id:
                continue
            ref = str(model.get("ref") or format_model_ref(provider_env, model_id))
            catalog[ref] = {
                "ref": ref,
                "model": model_id,
                "provider_env": provider_env,
                "provider_label": provider_label,
                "display_name": model_ref_label(provider_label, model_id),
                "thinking_modes": list(model.get("thinking_modes") or []),
                "thinking_parameter": model.get("thinking_parameter") or "reasoning_effort",
            }
            for agent_id in agent_models:
                allowed = (agent_provider_envs or {}).get(agent_id)
                if allowed is None or provider_env in allowed:
                    dynamic_options.setdefault(agent_id, []).append(ref)

    active_model_refs: dict[str, str] = {}
    model_options: dict[str, list[str]] = {}
    for agent_id, active_value in agent_models.items():
        requested_provider, model_id = parse_model_ref(active_value)
        active_ref = str(active_value or "").strip()
        if requested_provider:
            active_ref = format_model_ref(requested_provider, model_id)
        else:
            allowed = (agent_provider_envs or {}).get(agent_id)
            matching_refs = [
                ref
                for ref, item in catalog.items()
                if item.get("model") == model_id
                and (allowed is None or item.get("provider_env") in allowed)
            ]
            if len(matching_refs) == 1:
                active_ref = matching_refs[0]
        if not active_ref:
            active_ref = model_id
        active_model_refs[agent_id] = active_ref

        # 使用者勾選的清單是權威來源：有勾選紀錄時不再自動灌入供應商全量
        # 清單（否則 × 刪除會在下一次 capabilities 抓取時被復活）。沒有勾選
        # 紀錄的 agent 維持舊行為（動態供應商清單 → 預設清單）。
        persisted = [
            str(item).strip()
            for item in ((fallback_options or {}).get(agent_id) or [])
            if str(item).strip()
        ]
        if persisted:
            options = persisted
        else:
            options = list(dynamic_options.get(agent_id) or [])
            if not options:
                options = [
                    str(item).strip()
                    for item in (fallback_options or {}).get(agent_id, [])
                    if str(item).strip()
                ]
        if active_ref and active_ref not in options:
            options.insert(0, active_ref)
        deduped: list[str] = []
        for option in options:
            if option in deduped:
                continue
            deduped.append(option)
            if option not in catalog:
                _, option_model = parse_model_ref(option)
                catalog[option] = {
                    "ref": option,
                    "model": option_model,
                    "provider_env": parse_model_ref(option)[0],
                    "provider_label": "自動路由",
                    "display_name": model_ref_label(None, option_model),
                    "thinking_modes": [],
                    "thinking_parameter": "reasoning_effort",
                }
        model_options[agent_id] = deduped

    return {
        "options": model_options,
        "catalog": catalog,
        "active_model_refs": active_model_refs,
    }
