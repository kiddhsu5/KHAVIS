#!/usr/bin/env python3
"""Autonomous Agentic CLI Shim for Antigravity, MiniMax Code, and OpenCode.

Installed into ``/usr/local/bin/{antigravity,minimax,opencode}`` by ``deploy/install.sh``
when native vendor CLIs are not present on a cloud VM. Automatically re-executes inside
``$KHAVIS_HOME/.venv/bin/python`` if invoked by system Python without virtualenv packages.

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _resolve_khavis_home() -> Path:
    env_home = os.environ.get("KHAVIS_HOME", "").strip()
    if env_home and Path(env_home).is_dir():
        return Path(env_home).resolve()
    here = Path(__file__).resolve()
    if here.parent.name == "deploy" and (here.parent.parent / "bot").is_dir():
        return here.parent.parent
    for candidate in (Path("/opt/khavis"), Path.home() / "khavis"):
        if (candidate / "bot").is_dir():
            return candidate
    return Path.home() / "khavis"


def _ensure_venv_python(khavis_home: Path) -> None:
    venv_py = khavis_home / ".venv" / "bin" / "python"
    if not venv_py.is_file():
        return
    try:
        import pydantic  # noqa: F401, PLC0415
    except ImportError:
        if Path(sys.executable).resolve() != venv_py.resolve():
            os.execv(str(venv_py), [str(venv_py), *sys.argv])


def _load_dotenv(khavis_home: Path) -> None:
    for env_file in (khavis_home / ".env", Path.home() / ".hermes" / ".env"):
        if not env_file.is_file():
            continue
        for raw_line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip().strip('"').strip("'")
            if key and val and not os.environ.get(key):
                os.environ[key] = val


def main() -> int:
    prog = (os.environ.get("KHAVIS_SHIM_NAME") or Path(sys.argv[0]).name).lower()
    if "opencode" in prog or "zcode" in prog:
        agent_name = "opencode"
        ver_str = "opencode 1.3.0 (GLM-5.3 Coding Plan Autonomous CLI)"
        default_model = "glm-5.3-flash"
        env_key = "OPENCODE_DISPATCH_MODEL"
    elif "minimax" in prog or "mcode" in prog:
        agent_name = "minimax"
        ver_str = "minimax 1.5.0 (MiniMax-M3.1-Flash Autonomous Coding CLI)"
        default_model = "MiniMax-M3.1-Flash"
        env_key = "MINIMAX_DISPATCH_MODEL"
    else:
        agent_name = "antigravity"
        ver_str = "antigravity 2.1.0 (Google Antigravity AGY Autonomous CLI)"
        default_model = "antigravity-pro"
        env_key = "ANTIGRAVITY_DISPATCH_MODEL"

    args = sys.argv[1:]
    if not args or "--version" in args or "-v" in args:
        print(ver_str)
        return 0

    khavis_home = _resolve_khavis_home()
    _ensure_venv_python(khavis_home)
    if str(khavis_home) not in sys.path:
        sys.path.insert(0, str(khavis_home))
    _load_dotenv(khavis_home)

    model = os.environ.get(env_key, default_model)
    thinking_mode = os.environ.get("KHAVIS_THINKING_MODE") or None

    prompt_parts: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("exec", "run") and i == 0:
            i += 1
            continue
        if a in ("-m", "--model") and i + 1 < len(args):
            model = args[i + 1]
            i += 2
            continue
        if a in ("--output-format", "--effort") and i + 1 < len(args):
            if a == "--effort":
                thinking_mode = args[i + 1]
            i += 2
            continue
        if a == "--":
            prompt_parts.extend(args[i + 1 :])
            break
        prompt_parts.append(a)
        i += 1

    prompt = " ".join(prompt_parts).strip()
    if not prompt:
        print(json.dumps({"error": "Empty prompt"}))
        return 1

    from bot.backends._http_fallback import call_upstream_llm_fallback  # noqa: PLC0415

    res = call_upstream_llm_fallback(
        prompt,
        model=model,
        agent_name=agent_name,
        timeout_s=60,
        cwd=os.getcwd(),
        enable_tools=True,
        thinking_mode=thinking_mode,
    )
    if res.get("ok"):
        print(
            json.dumps(
                {
                    "text": res["text"],
                    "model": res.get("model") or model,
                    "usage": {
                        "prompt_tokens": res.get("tokens_in", 0),
                        "completion_tokens": res.get("tokens_out", 0),
                    },
                },
                ensure_ascii=False,
            )
        )
        return 0

    sys.stderr.write(f"{agent_name} error: {res.get('error', 'Unknown error')}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
