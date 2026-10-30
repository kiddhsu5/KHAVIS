"""User-Defined Custom Agent Registry (`config/custom_agents.json`).

Allows users to dynamically register, update, and remove their own CLI or HTTP
agents (e.g. Aider, OpenCode, Goose, DeepSeek, Qwen, Local Ollama, or custom scripts)
from the Web Workbench, and dispatches them alongside built-in agents in the shared
project workspace or isolated Git Worktrees.

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_AGENTS_PATH = PROJECT_ROOT / "config" / "custom_agents.json"

RESERVED_AGENT_IDS = {
    "claude",
    "codex",
    "antigravity",
    "minimax",
    "opencode",
    "khavis",
    "kavis-tencent",
    "judge",
}
_AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,31}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:\-/]{1,80}$")


def load_custom_agents() -> list[dict[str, Any]]:
    """Load persisted user-defined custom agents from config/custom_agents.json."""
    if not CUSTOM_AGENTS_PATH.is_file():
        return []
    try:
        raw = json.loads(CUSTOM_AGENTS_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [a for a in raw if isinstance(a, dict) and a.get("id")]
    except Exception:  # noqa: BLE001
        pass
    return []


def save_custom_agents(agents: list[dict[str, Any]]) -> None:
    """Persist custom agents list to config/custom_agents.json."""
    CUSTOM_AGENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_AGENTS_PATH.write_text(
        json.dumps(agents, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def register_or_update_custom_agent(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and register (or update) a user-defined custom agent."""
    agent_id = str(payload.get("id") or "").strip().lower()
    if not _AGENT_ID_RE.match(agent_id):
        raise ValueError(
            "Agent ID 必須為 2~32 個小寫英數字、底線或連字號 (例如: aider, opencode, my-agent)"
        )
    if agent_id in RESERVED_AGENT_IDS:
        raise ValueError(f"Agent ID '{agent_id}' 為系統內建保留代號，請使用其他代號")

    name = str(payload.get("name") or agent_id).strip()[:48]
    agent_type = str(payload.get("type") or "cli").strip().lower()
    if agent_type not in ("cli", "http"):
        agent_type = "cli"

    binary = str(payload.get("binary") or agent_id).strip()[:120]
    args_template = str(payload.get("args_template") or "--model {model} -- {prompt}").strip()[:240]
    model = str(payload.get("model") or "MiniMax-M3.1-Flash").strip()
    if not _MODEL_RE.match(model):
        raise ValueError(f"無效的模型名稱: {model!r}")

    raw_options = payload.get("options")
    if isinstance(raw_options, list):
        options = [
            str(x).strip()
            for x in raw_options
            if str(x).strip() and _MODEL_RE.match(str(x).strip())
        ]
    elif isinstance(raw_options, str):
        options = [
            x.strip() for x in raw_options.split(",") if x.strip() and _MODEL_RE.match(x.strip())
        ]
    else:
        options = []
    if model not in options:
        options.insert(0, model)
    for fallback_opt in ("MiniMax-M3.1-Flash", "glm-5.3-flashx"):
        if fallback_opt not in options:
            options.append(fallback_opt)

    api_key_env = str(payload.get("api_key_env") or "MINIMAX_API_KEY").strip()[:64]
    base_url = str(payload.get("base_url") or "").strip()[:200]
    system_prompt = str(payload.get("system_prompt") or "").strip()[:500]
    desc = str(payload.get("desc") or f"用戶自訂 Agent ({agent_type.upper()}: {binary})").strip()[
        :120
    ]
    icon = str(payload.get("icon") or "fa-plug").strip()[:32]
    color = str(payload.get("color") or "#EC4899").strip()[:16]

    record: dict[str, Any] = {
        "id": agent_id,
        "name": name,
        "type": agent_type,
        "binary": binary,
        "args_template": args_template,
        "model": model,
        "default_model": model,
        "options": options,
        "api_key_env": api_key_env,
        "base_url": base_url,
        "system_prompt": system_prompt,
        "desc": desc,
        "tier": "Custom",
        "icon": icon,
        "color": color,
        "adjustable": True,
        "custom": True,
    }

    existing = load_custom_agents()
    updated_list: list[dict[str, Any]] = []
    replaced = False
    for item in existing:
        if item.get("id") == agent_id:
            updated_list.append(record)
            replaced = True
        else:
            updated_list.append(item)
    if not replaced:
        updated_list.append(record)

    save_custom_agents(updated_list)
    return enrich_custom_agent_status(record)


def delete_custom_agent(agent_id: str) -> list[dict[str, Any]]:
    """Remove a user-defined custom agent by ID."""
    target = (agent_id or "").strip().lower()
    existing = load_custom_agents()
    filtered = [a for a in existing if a.get("id") != target]
    save_custom_agents(filtered)
    return [enrich_custom_agent_status(a) for a in filtered]


def enrich_custom_agent_status(agent: dict[str, Any]) -> dict[str, Any]:
    """Attach live installation / readiness metadata to a custom agent dict."""
    item = dict(agent)
    agent_type = item.get("type", "cli")
    binary = str(item.get("binary") or item.get("id") or "")
    bin_first = binary.split()[0] if binary else ""
    bin_path = shutil.which(bin_first) if bin_first and agent_type == "cli" else None

    item["custom"] = True
    item["adjustable"] = True
    item["plan"] = f"Custom ({item.get('api_key_env') or 'Auto Routing'})"
    if agent_type == "http":
        item["installed"] = True
        item["path"] = item.get("base_url") or "OpenAI/Anthropic Compatible HTTP"
        item["status"] = "ready"
        item["version"] = "HTTP API Agent Ready"
    elif bin_path:
        item["installed"] = True
        item["path"] = bin_path
        item["status"] = "ready"
        item["version"] = "Custom CLI Ready"
    else:
        item["installed"] = True
        item["path"] = f"API Fallback ({bin_first} -> {item.get('model')})"
        item["status"] = "ready"
        item["version"] = "Hybrid CLI / API Ready"
    return item
