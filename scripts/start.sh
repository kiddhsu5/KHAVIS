#!/usr/bin/env bash
# Startup script for khavis.
#
# Loads the plugin registry, validates every pool, starts the watchdog
# hot-reloader on config/*.yaml, and prints a health snapshot. Designed
# to be safe to run even when API keys are not set: it will simply
# report ok=false for any pool that requires credentials.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

CONFIG_DIR="$PROJECT_ROOT/config"
CAPS_FILE="$CONFIG_DIR/capabilities.yaml"
POOLS_FILE="$CONFIG_DIR/pools.yaml"

export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

echo "=========================================="
echo " K.H.A.V.I.S. starting"
echo "   project root: $PROJECT_ROOT"
echo "   python:       $(python3 --version 2>/dev/null || echo 'not found')"
echo "=========================================="

python3 - "$PROJECT_ROOT" "$CAPS_FILE" <<'PY'
"""Load registry, hot-reload YAMLs, and print health snapshot."""
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

from core.capability_router import CapabilityRouter
from core.env import load_env
from core.hot_reload import HotReloader
from core.registry import PluginRegistry

load_env()

cfg_dir = root / "config"

registry = PluginRegistry(root).discover()
registry.apply_pools_config(cfg_dir / "pools.yaml")
summary = registry.summary()
print(f"\n[registry] discovered {summary['count']} plugin pools")
for name in summary["names"]:
    print(f"  - {name}")
if summary["errors"]:
    print("[registry] warnings:")
    for err in summary["errors"]:
        print(f"  ! {err}")

router = CapabilityRouter(registry)
try:
    router.load_capabilities(cfg_dir / "capabilities.yaml")
    print(f"\n[capabilities] loaded {len(router.entries())} entries")
except Exception as exc:
    print(f"[capabilities] failed to load: {exc!r}")

print("\n[health] per-pool snapshot:")
print(f"  {'POOL':<22} {'OK':<5} DETAIL")
print(f"  {'-'*22} {'-'*5} {'-'*40}")
for plugin in registry.all():
    try:
        h = plugin.health_check()
    except Exception as exc:
        h = {"ok": False, "detail": f"health_check raised: {exc!r}"}
    ok = "yes" if h.get("ok") else "no"
    print(f"  {plugin.name:<22} {ok:<5} {h.get('detail', '')}")

def reload(path: Path) -> None:
    print(f"\n[hot_reload] config changed: {path.name}; reloading…")
    try:
        registry.discover()
        registry.apply_pools_config(cfg_dir / "pools.yaml")
        router.load_capabilities(cfg_dir / "capabilities.yaml")
        print("[hot_reload] reload ok")
    except Exception as exc:
        print(f"[hot_reload] reload failed: {exc!r}")

reloader = HotReloader(
    paths=[cfg_dir / "capabilities.yaml", cfg_dir / "pools.yaml"],
    callback=reload,
    debounce_seconds=1.0,
)
if not reloader.is_available():
    print("\n[hot_reload] watchdog not installed — skipping live watch")
else:
    reloader.start()
    print("\n[hot_reload] watching config/*.yaml (Ctrl-C to stop)")
    try:
        import time
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        reloader.stop()
        print("\n[hot_reload] stopped")
PY
