#!/usr/bin/env python3
"""Live API smoke checks — real credentials, minimal tokens.

Run locally or on the ECS:

    python scripts/live_api_check.py

Prints one line per pool with OK/FAIL and latency. Never prints keys.
Exit code 0 if at least one pool is OK and no unexpected exception.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.registry import PluginRegistry  # noqa: E402


def main() -> int:
    reg = PluginRegistry(ROOT).discover().apply_pools_config(ROOT / "config" / "pools.yaml")
    reg.apply_model_overrides(ROOT / "config" / "models.yaml")

    prompt = [{"role": "user", "content": "Reply with exactly: PONG"}]
    ok = 0
    fail = 0
    skipped = 0
    print(f"{'pool':22} {'result':8} {'ms':>7}  detail")
    for plugin in sorted(reg.all(), key=lambda p: p.name):
        health = plugin.health_check()
        if not health.get("ok"):
            skipped += 1
            print(f"{plugin.name:22} {'SKIP':8} {'':>7}  {str(health.get('detail'))[:80]}")
            continue
        t0 = time.perf_counter()
        try:
            import multiprocessing as mp
            import os

            # Bind `plugin` as a default argument. The closure otherwise closes
            # over the loop variable, which (a) breaks outright under the `spawn`
            # start method — closures are not picklable — and (b) keeps advancing
            # while the loop moves to the next pool.
            def _call(q, _plugin=plugin):
                try:
                    q.put({"ok": True, "out": _plugin.chat(prompt, max_tokens=32)})
                except Exception as exc:  # noqa: BLE001
                    q.put({"ok": False, "err": f"{type(exc).__name__}: {exc}"})

            q = mp.Queue()
            proc = mp.Process(target=_call, args=(q,))
            proc.start()
            proc.join(int(os.environ.get("LIVE_TIMEOUT_S", "20")))
            if proc.is_alive():
                proc.terminate()
                proc.join(3)
                raise TimeoutError(f"timed out after {os.environ.get('LIVE_TIMEOUT_S', '20')}s")
            if q.empty():
                raise RuntimeError("no result from child")
            payload = q.get()
            if not payload.get("ok"):
                raise RuntimeError(payload.get("err") or "unknown")
            out = payload["out"]
            ms = int((time.perf_counter() - t0) * 1000)
            text = ""
            choices = out.get("choices") or []
            if choices:
                text = (choices[0].get("message") or {}).get("content") or ""
            usage = out.get("usage") or {}
            ok += 1
            print(
                f"{plugin.name:22} {'OK':8} {ms:7d}  "
                f"model={out.get('model')} tok={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} "
                f"reply={text[:40]!r}"
            )
        except Exception as exc:  # noqa: BLE001
            ms = int((time.perf_counter() - t0) * 1000)
            fail += 1
            print(f"{plugin.name:22} {'FAIL':8} {ms:7d}  {type(exc).__name__}: {str(exc)[:100]}")

    print(f"\nsummary: ok={ok} fail={fail} skipped={skipped}")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
