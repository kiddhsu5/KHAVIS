"""Role definitions for the multi-agent team.

Roles are configurable via ``config/agents.yaml`` so behaviour can be tuned
without touching code. Each :class:`Role` carries the minimum metadata
needed for the :mod:`agents.agent_factory` to materialise a LangChain
runnable for that persona.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Structural defaults, used only when config/agents.yaml has no entry.
#
# This table used to carry a second copy of every ``system_prompt``. Two
# copies of the same prose drift silently: edit the YAML, delete an entry,
# and the role reverts to a stale prompt here without any error -- the role
# still loads, it just stops being the role you configured. ``config/`` is
# force-included into the wheel, so the YAML is the single source of truth
# for prompt text; this table supplies only the non-prose defaults.
#
# A role missing from the YAML is announced loudly rather than quietly
# served from a stale copy.
# ----------------------------------------------------------------------
_BUILTIN: dict[str, dict[str, Any]] = {
    "planner": {
        "display_name": "Planner",
        "capability_required": "理論",
        "temperature": 0.2,
        "max_tokens": 2048,
        "max_iterations": 1,
        "style_hint": "",
    },
    "coder_a": {
        "display_name": "CoderA",
        "capability_required": "程式碼",
        "temperature": 0.3,
        "max_tokens": 3072,
        "max_iterations": 1,
        "style_hint": "clean code",
    },
    "coder_b": {
        "display_name": "CoderB",
        "capability_required": "程式碼",
        "temperature": 0.3,
        "max_tokens": 3072,
        "max_iterations": 1,
        "style_hint": "defensive code",
    },
    "debater": {
        "display_name": "Debater",
        "capability_required": "辩論",
        "temperature": 0.4,
        "max_tokens": 2048,
        "max_iterations": 2,
        "style_hint": "neutral",
    },
    "critic": {
        "display_name": "Critic",
        "capability_required": "審查",
        "temperature": 0.2,
        "max_tokens": 2048,
        "max_iterations": 1,
        "style_hint": "sharp",
    },
    "verifier": {
        "display_name": "Verifier",
        "capability_required": "驗證",
        "temperature": 0.1,
        "max_tokens": 2048,
        # Upper bound on verifier runs (critic <-> verifier retry loop).
        "max_iterations": 3,
        "style_hint": "reliable",
    },
    "learner": {
        "display_name": "Learner",
        "capability_required": "理論",
        "temperature": 0.2,
        "max_tokens": 512,
        "max_iterations": 1,
        "style_hint": "",
    },
}

#: Used when a role has no YAML entry at all. It says so, rather than
#: pretending to be a configured persona.
_UNCONFIGURED_PROMPT = (
    "（此角色未在 config/agents.yaml 中設定 system_prompt，"
    "因此沒有角色指示。"
    "請依任務描述直接作答，"
    "並在輸出末尾說明缺少角色設定。）"
)


@dataclass
class Role:
    """A single role/persona in the multi-agent team."""

    name: str
    display_name: str
    system_prompt: str
    capability_required: str
    temperature: float = 0.3
    max_tokens: int = 1024
    max_iterations: int = 1
    style_hint: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Role:
        return cls(
            name=str(data["name"]),
            display_name=str(data.get("display_name", data["name"])),
            system_prompt=str(data.get("system_prompt", "")),
            capability_required=str(data.get("capability_required", "推理")),
            temperature=float(data.get("temperature", 0.3)),
            max_tokens=int(data.get("max_tokens", 1024)),
            max_iterations=int(data.get("max_iterations", 1)),
            style_hint=str(data.get("style_hint", "")),
            extra={k: v for k, v in data.items() if k not in _ROLE_KEYS},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "system_prompt": self.system_prompt,
            "capability_required": self.capability_required,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_iterations": self.max_iterations,
            "style_hint": self.style_hint,
            **({"extra": dict(self.extra)} if self.extra else {}),
        }


_ROLE_KEYS = {
    "name",
    "display_name",
    "system_prompt",
    "capability_required",
    "temperature",
    "max_tokens",
    "max_iterations",
    "style_hint",
}


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def _default_config_path() -> Path:
    """Return path to the shipped YAML config."""
    return Path(__file__).resolve().parent.parent / "config" / "agents.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw.get("roles") or {}


def load_role(name: str, config_path: Path | None = None) -> Role:
    """Load a role by name from ``config/agents.yaml``.

    The YAML is the only source of ``system_prompt``. When a role has no
    entry there we fall back to structural defaults and *say so* — the old
    behaviour served a second, stale copy of the prompt from this module,
    so a role removed from the config still loaded with old instructions and
    nothing indicated a mismatch.
    """
    path = Path(config_path) if config_path else _default_config_path()
    yaml_roles = _load_yaml(path)

    data = yaml_roles.get(name)
    if data is None:
        base = _BUILTIN.get(name)
        if base is None:
            raise KeyError(f"unknown role: {name!r}")
        log.warning(
            "role %r has no entry in %s — falling back to structural defaults with no "
            "system prompt. Add the entry to the YAML, or fix the config path if the "
            "file is meant to be there.",
            name,
            path,
        )
        data = {**base, "name": name, "system_prompt": _UNCONFIGURED_PROMPT}
    return Role.from_dict(data)


def list_roles(config_path: Path | None = None) -> list[str]:
    """Return all known role names (built-in + configured)."""
    yaml_roles = _load_yaml(Path(config_path) if config_path else _default_config_path())
    seen = set(_BUILTIN.keys())
    seen.update(yaml_roles.keys())
    return sorted(seen)


def create_role(name: str, **kwargs: Any) -> Role:
    """Construct a role in code, optionally overriding fields."""
    base = _BUILTIN.get(name)
    if base is None:
        base = {
            "name": name,
            "display_name": name,
            "system_prompt": "",
            "capability_required": "推理",
        }
    merged = {**base, **kwargs, "name": name}
    return Role.from_dict(merged)


__all__ = ["Role", "load_role", "list_roles", "create_role"]
