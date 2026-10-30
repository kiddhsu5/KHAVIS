"""Plugin registry with auto-discovery.

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

import copy
import importlib
import importlib.util
import inspect
import logging
import os
import pkgutil
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from providers.base import ProviderPlugin

log = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

#: Upper bound per error bucket so repeated hot reloads cannot grow memory.
_MAX_ERRORS = 200

_MISSING: Any = object()


def _unset_placeholders(value: str) -> list[str]:
    """Names of ``${VAR}`` placeholders in ``value`` that are not set."""
    return [m.group(1) for m in _PLACEHOLDER_RE.finditer(value) if os.getenv(m.group(1)) is None]


def _expand_env(value: str) -> str:
    """Expand ``${VAR}`` placeholders against ``os.environ``.

    Unset variables are left as the literal ``${VAR}`` (callers warn).
    """

    def repl(match: re.Match[str]) -> str:
        return os.getenv(match.group(1), match.group(0))

    return _PLACEHOLDER_RE.sub(repl, value)


# Files that should never be auto-loaded as plugins.
_SKIP_FILES = {"__init__.py", "base.py"}

# Symbols we never want to treat as plugin entry points.
_SKIP_NAMES = {"ProviderPlugin", "OpenAI", "genai", "requests"}


def _project_root() -> Path:
    """Return the K.H.A.V.I.S. project root (one level above this file)."""
    return Path(__file__).resolve().parent.parent


class PluginRegistry:
    """Auto-discovers and instantiates every :class:`ProviderPlugin`."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root: Path = Path(project_root) if project_root else _project_root()
        self._plugins: dict[str, ProviderPlugin] = {}
        self._factories: list[Any] = []
        # Discovery errors. Reset on every ``discover()``.
        self._errors: list[str] = []
        # Per-config-file errors; each ``apply_*`` call resets its own bucket
        # so re-applying (hot reload) never accumulates stale messages.
        self._apply_errors: dict[str, list[str]] = {}
        # Original values captured before models.yaml touched a pool, so a
        # removed override can be rolled back. pool -> field -> value.
        self._model_baseline: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(self) -> PluginRegistry:
        """Scan the ``providers/`` directory and load every plugin module.

        Returns ``self`` so calls can be chained::

            registry = PluginRegistry().discover()
        """
        self._errors = []
        providers_dir = self.project_root / "providers"
        if not providers_dir.exists():
            self._errors.append(f"providers dir not found: {providers_dir}")
            return self

        # Ensure ``providers`` is importable as a package.
        parent = str(self.project_root)
        if parent not in sys.path:
            sys.path.insert(0, parent)

        for module_info in pkgutil.iter_modules([str(providers_dir)]):
            name = module_info.name
            if name in _SKIP_FILES or name.startswith("_"):
                continue
            try:
                module = importlib.import_module(f"providers.{name}")
            except Exception as exc:  # pragma: no cover - defensive
                self._errors.append(f"failed to import providers.{name}: {exc!r}")
                continue
            self._harvest(module)
        return self

    def _harvest(self, module: Any) -> None:
        """Pull plugin classes and factory callables out of a module."""
        for attr_name, obj in vars(module).items():
            if attr_name in _SKIP_NAMES or attr_name.startswith("_"):
                continue
            if (
                inspect.isclass(obj)
                and issubclass(obj, ProviderPlugin)
                and obj is not ProviderPlugin
            ):
                if getattr(obj, "_skip_auto_instantiate", False):
                    # Only factories should produce this class.
                    continue
                # Multi-instance plugin: one pool per entry of _plugin_instances.
                instances_cfg = getattr(obj, "_plugin_instances", None)
                if isinstance(instances_cfg, list) and instances_cfg:
                    for kwargs in instances_cfg:
                        if not isinstance(kwargs, dict):
                            self._errors.append(
                                f"{attr_name}._plugin_instances entries must be dicts"
                            )
                            continue
                        try:
                            instance = obj(**kwargs)
                        except Exception as exc:  # pragma: no cover - defensive
                            self._errors.append(
                                f"failed to instantiate {attr_name}(**{kwargs!r}): {exc!r}"
                            )
                            continue
                        self._register(instance)
                else:
                    try:
                        instance = obj()
                    except Exception as exc:  # pragma: no cover - defensive
                        self._errors.append(f"failed to instantiate {attr_name}: {exc!r}")
                        continue
                    self._register(instance)
            elif callable(obj) and hasattr(obj, "provider_id") and not inspect.isclass(obj):
                # e.g. VolcanoPluginFactory — callable that returns list[Plugin].
                self._factories.append(obj)

        # Run factories we discovered. ``_factories`` accumulates across
        # modules — run each callable at most once.
        for factory in list(self._factories):
            if getattr(self, "_ran_factories", None) is None:
                self._ran_factories = set()  # type: ignore[var-annotated]
            if factory in self._ran_factories:
                continue
            self._ran_factories.add(factory)
            try:
                produced = factory()
            except Exception as exc:  # pragma: no cover - defensive
                self._errors.append(f"factory {factory!r} failed: {exc!r}")
                continue
            for instance in produced or []:
                if isinstance(instance, ProviderPlugin):
                    self._register(instance)

    def _register(self, plugin: ProviderPlugin) -> None:
        if plugin.name in self._plugins:
            # Preserve first registration but record the conflict.
            self._errors.append(f"duplicate plugin name: {plugin.name}")
            return
        self._plugins[plugin.name] = plugin

    # ------------------------------------------------------------------
    # pools.yaml overrides
    # ------------------------------------------------------------------
    def _apply_error(self, bucket: str, message: str) -> None:
        errs = self._apply_errors.setdefault(bucket, [])
        if len(errs) < _MAX_ERRORS:
            errs.append(message)

    def apply_pools_config(self, pools_yaml_path: Path | str) -> PluginRegistry:
        """Override plugin fields from a ``pools.yaml`` file.

        Each entry in ``pools:`` is matched against an already-discovered
        plugin by its ``name``. Fields supported:

        - ``endpoint``     → ``plugin.default_endpoint`` (``${VAR}`` expanded;
                             ``null`` leaves the plugin default untouched)
        - ``model``        → ``plugin.model`` (str or list; first wins)
        - ``env_key``      → ``plugin.api_key`` (taken from ``os.getenv``;
                             if the env var is missing the plugin's existing
                             api_key is left untouched)
        - ``region``/``tier``/``notes`` → ``plugin.metadata`` (merged)
        - ``models``       → if ``model`` is absent and ``models`` is a list,
                             the first element is used as ``plugin.model``
        - ``disabled: true`` → the pool is **removed from the registry**.

        ``discover()`` instantiates every plugin it finds under ``providers/``,
        so deleting or commenting out a ``pools.yaml`` entry does not stop a
        pool from being served — the file only *overrides* discovered plugins.
        Without ``disabled`` there was therefore no way to retire a pool whose
        upstream retired (404/410), short of moving the plugin out of the
        package. The name is recorded in :meth:`errors` so a typo is visible.

        Unknown pool names, malformed entries and unset ``${VAR}``
        placeholders are recorded in :meth:`errors` (the bucket is reset on
        every call) and skipped; one bad entry never stops the others.
        Returns ``self`` for chaining.
        """
        self._apply_errors["pools"] = []
        path = Path(pools_yaml_path)
        if not path.exists():
            self._apply_error("pools", f"pools.yaml not found: {path}")
            return self
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # pragma: no cover - defensive
            self._apply_error("pools", f"failed to parse {path}: {exc!r}")
            return self
        if not isinstance(data, dict):
            self._apply_error("pools", f"{path}: top level must be a mapping")
            return self

        pools = data.get("pools") or []
        if not isinstance(pools, list):
            self._apply_error("pools", f"{path}: 'pools' must be a list")
            return self
        for index, entry in enumerate(pools):
            if not isinstance(entry, dict):
                self._apply_error("pools", f"pools.yaml entry #{index} is not a mapping: {entry!r}")
                continue
            try:
                self._apply_pool_entry(entry)
            except Exception as exc:  # noqa: BLE001 - one bad entry must not stop the rest
                self._apply_error(
                    "pools", f"pools.yaml entry {entry.get('name', index)!r} failed: {exc!r}"
                )
        return self

    def _apply_pool_entry(self, entry: dict[str, Any]) -> None:
        name = entry.get("name")
        if not name:
            return
        key = str(name)

        if entry.get("disabled") is True:
            if self._plugins.pop(key, None) is not None:
                self._apply_error("pools", f"pool {key!r} disabled via pools.yaml")
            else:
                # Disabling something that was never discovered is almost always
                # a typo in the name; say so rather than silently doing nothing.
                self._apply_error("pools", f"pools.yaml disables unknown pool: {key!r} (typo?)")
            return

        plugin = self._plugins.get(key)
        if plugin is None:
            self._apply_error("pools", f"pools.yaml references unknown pool: {key!r}")
            return

        endpoint = entry.get("endpoint")
        if endpoint is not None:
            raw = str(endpoint)
            missing = _unset_placeholders(raw)
            if missing:
                msg = (
                    f"warning: pools.yaml {name!r} endpoint uses unset env var(s) "
                    f"{', '.join(missing)}"
                )
                log.warning(msg)
                self._apply_error("pools", msg)
            plugin.default_endpoint = _expand_env(raw)

        model_value = entry.get("model")
        if model_value is None and isinstance(entry.get("models"), list) and entry["models"]:
            model_value = entry["models"][0]
        if isinstance(model_value, list):
            model_value = model_value[0] if model_value else None
        if model_value is not None:
            plugin.model = str(model_value)

        env_key = entry.get("env_key")
        if env_key:
            api_key = os.getenv(str(env_key))
            if api_key:
                plugin.api_key = api_key

        meta_overlay: dict[str, Any] = {}
        for k in ("region", "tier", "notes"):
            if k in entry:
                meta_overlay[k] = entry[k]
        if meta_overlay:
            plugin.metadata.update(meta_overlay)

    # ------------------------------------------------------------------
    # models.yaml overrides
    # ------------------------------------------------------------------
    def _snapshot_model_fields(self, plugin: ProviderPlugin) -> dict[str, Any]:
        base = self._model_baseline.get(plugin.name)
        if base is None:
            customs = plugin.metadata.get("custom_models", _MISSING)
            base = {
                "model": plugin.model,
                "model_notes": plugin.metadata.get("model_notes", _MISSING),
                "custom_models": customs if customs is _MISSING else copy.deepcopy(customs),
            }
            self._model_baseline[plugin.name] = base
        return base

    def _restore_model_overrides(self) -> None:
        """Undo the previous ``apply_model_overrides`` (only fields still ours)."""
        for pool, base in list(self._model_baseline.items()):
            plugin = self._plugins.get(pool)
            if plugin is None:
                continue
            applied = base.get("applied", {})
            # Roll back only values nobody else changed since we set them
            # (e.g. a later apply_pools_config wins over a stale baseline).
            if "model" in applied and plugin.model == applied["model"]:
                plugin.model = base["model"]
            if (
                "model_notes" in applied
                and plugin.metadata.get("model_notes") == applied["model_notes"]
            ):
                if base["model_notes"] is _MISSING:
                    plugin.metadata.pop("model_notes", None)
                else:
                    plugin.metadata["model_notes"] = base["model_notes"]
            if "custom_models" in applied:
                if base["custom_models"] is _MISSING:
                    plugin.metadata.pop("custom_models", None)
                else:
                    plugin.metadata["custom_models"] = copy.deepcopy(base["custom_models"])
        self._model_baseline.clear()

    def apply_model_overrides(self, models_yaml_path: Path | str) -> PluginRegistry:
        """Apply fine-tune / model overrides from ``config/models.yaml`` (v0.5.0).

        ``overrides`` entries reshape an existing pool's ``model`` field;
        ``custom_models`` entries are recorded in metadata so the dashboard
        can surface them.

        Idempotent: every call first rolls back what the previous call set
        (original values are remembered), then applies the file afresh — so
        re-applying never duplicates ``custom_models`` and a removed
        override restores the pool's original model. A missing file means
        "no overrides" (everything is rolled back); a file that fails to
        parse leaves the current state untouched.
        """
        self._apply_errors["models"] = []
        path = Path(models_yaml_path)
        if not path.exists():
            self._restore_model_overrides()
            return self
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # pragma: no cover - defensive
            self._apply_error("models", f"failed to parse {path}: {exc!r}")
            return self
        if not isinstance(data, dict):
            self._apply_error("models", f"{path}: top level must be a mapping")
            return self

        self._restore_model_overrides()

        for entry in data.get("overrides") or []:
            if not isinstance(entry, dict):
                self._apply_error("models", f"models.yaml override is not a mapping: {entry!r}")
                continue
            pool = entry.get("pool")
            plugin = self._plugins.get(pool) if pool else None
            if plugin is None:
                if pool:
                    self._apply_error("models", f"models.yaml references unknown pool: {pool!r}")
                continue
            base = self._snapshot_model_fields(plugin)
            applied = base.setdefault("applied", {})
            if entry.get("model"):
                plugin.model = str(entry["model"])
                applied["model"] = plugin.model
            if entry.get("notes"):
                plugin.metadata["model_notes"] = str(entry["notes"])
                applied["model_notes"] = plugin.metadata["model_notes"]
        for entry in data.get("custom_models") or []:
            if not isinstance(entry, dict):
                self._apply_error("models", f"models.yaml custom_model is not a mapping: {entry!r}")
                continue
            pool = entry.get("pool")
            plugin = self._plugins.get(pool) if pool else None
            if plugin is None:
                if pool:
                    self._apply_error("models", f"models.yaml references unknown pool: {pool!r}")
                continue
            base = self._snapshot_model_fields(plugin)
            base.setdefault("applied", {})["custom_models"] = True
            custom = plugin.metadata.setdefault("custom_models", [])
            if isinstance(custom, list) and entry not in custom:
                custom.append(dict(entry))
        return self

    def all(self) -> list[ProviderPlugin]:
        return list(self._plugins.values())

    def names(self) -> list[str]:
        return list(self._plugins.keys())

    def get(self, name: str) -> ProviderPlugin | None:
        return self._plugins.get(name)

    def by_capability(self, capability: str) -> list[ProviderPlugin]:
        return [p for p in self._plugins.values() if p.has_capability(capability)]

    def by_provider_id(self, provider_id: str) -> list[ProviderPlugin]:
        return [p for p in self._plugins.values() if p.provider_id == provider_id]

    def __iter__(self) -> Iterator[ProviderPlugin]:
        return iter(self._plugins.values())

    def __len__(self) -> int:
        return len(self._plugins)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._plugins

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def errors(self) -> list[str]:
        out = list(self._errors[:_MAX_ERRORS])
        for bucket in self._apply_errors.values():
            out.extend(bucket)
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "count": len(self._plugins),
            "names": self.names(),
            "errors": self.errors(),
        }
