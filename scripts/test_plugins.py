#!/usr/bin/env python3
"""Smoke-test every plugin: discover, instantiate, health-check.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

This script intentionally avoids issuing any real API calls. It only
verifies that:

    * every plugin module imports cleanly,
    * every plugin class instantiates without error,
    * health_check() returns a dict shaped ``{"ok": bool, "detail": str}``,
    * check_quota() returns the documented shape,
    * the ``name`` attribute is non-empty.

Exit code is non-zero if any plugin fails to load or has a malformed
health/quota response.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.registry import PluginRegistry  # noqa: E402
from providers.base import ProviderPlugin  # noqa: E402


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _check_dict_shape(name: str, value: Any, required: list[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        errors.append(f"{name}: expected dict, got {type(value).__name__}")
        return errors
    for key in required:
        if key not in value:
            errors.append(f"{name}: missing required key '{key}'")
    return errors


def _validate_plugin(plugin: ProviderPlugin) -> list[str]:
    errors: list[str] = []
    if not plugin.name:
        errors.append(f"{type(plugin).__name__}: missing 'name'")
    if not plugin.provider_id:
        errors.append(f"{type(plugin).__name__}: missing 'provider_id'")

    # health_check ------------------------------------------------------
    try:
        health = plugin.health_check()
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(f"{plugin.name}.health_check() raised: {exc!r}")
        health = None
    if health is not None:
        errors.extend(_check_dict_shape(f"{plugin.name}.health_check", health, ["ok", "detail"]))
        if isinstance(health, dict) and not isinstance(health.get("ok"), bool):
            errors.append(f"{plugin.name}.health_check['ok'] must be bool")

    # check_quota -------------------------------------------------------
    try:
        quota = plugin.check_quota()
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(f"{plugin.name}.check_quota() raised: {exc!r}")
        quota = None
    if quota is not None:
        errors.extend(
            _check_dict_shape(f"{plugin.name}.check_quota", quota, ["remaining", "total", "tier"])
        )

    # list_models -------------------------------------------------------
    try:
        models = plugin.list_models()
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(f"{plugin.name}.list_models() raised: {exc!r}")
    else:
        if not isinstance(models, list):
            errors.append(f"{plugin.name}.list_models() must return list")

    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    registry = PluginRegistry(PROJECT_ROOT).discover()
    summary = registry.summary()

    print(f"Discovered {summary['count']} plugins:")
    for name in summary["names"]:
        print(f"  - {name}")
    if summary["errors"]:
        print("\nRegistry-level errors:")
        for err in summary["errors"]:
            print(f"  ! {err}")

    all_errors: list[str] = list(summary["errors"])
    descriptions: list[dict[str, Any]] = []
    for plugin in registry.all():
        all_errors.extend(_validate_plugin(plugin))
        descriptions.append(plugin.describes())

    print("\nPlugin descriptions:")
    print(json.dumps(descriptions, indent=2, ensure_ascii=False))

    if all_errors:
        print("\nFAILURES:")
        for err in all_errors:
            print(f"  x {err}")
        return 1

    print("\nAll plugins loaded and validated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
