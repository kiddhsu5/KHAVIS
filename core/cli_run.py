"""Multi-agent collaborative dispatch for the ``khavis`` CLI.

Bridges the existing CLI (``core/cli.py``) to the multi-agent collaboration
layer (``core/collab_orchestrator.py`` + ``bot/backends/*``) so users can run
collaborative agent teams entirely from the terminal, without the Web
Workbench.

Subcommands added:

    khavis run "task"                       one-shot multi-agent dispatch
    khavis run "task" --agents claude,codex pick agents
    khavis run "task" --mode relay          collaboration mode
    khavis team                             interactive REPL (multi-turn)
    khavis agents                           list available agent backends

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from bot.backends import (
    AntigravityBackend,
    ClaudeBackend,
    CodexBackend,
    CommandCodeBackend,
    KavisTencentBackend,
    KhavisBackend,
    MinimaxBackend,
    OpencodeBackend,
)
from bot.dispatch import DispatchRouter
from bot.models import BackendName

from .env import load_env

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _build_backends(agent_models: dict[str, str] | None = None) -> dict[BackendName, Any]:
    """Assemble the standard backend dict, same as the web dispatch handler."""
    models = agent_models or {}
    return {
        "claude": ClaudeBackend(model=str(models.get("claude") or "").strip()),
        "codex": CodexBackend(model=str(models.get("codex") or "").strip()),
        "antigravity": AntigravityBackend(model=str(models.get("antigravity") or "").strip()),
        "minimax": MinimaxBackend(model=str(models.get("minimax") or "").strip()),
        "opencode": OpencodeBackend(model=str(models.get("opencode") or "").strip()),
        "cmdcode": CommandCodeBackend(model=str(models.get("cmdcode") or "").strip()),
        "khavis": KhavisBackend(),
    }


def _available_agents(backends: dict[str, Any]) -> list[dict[str, str]]:
    """Probe which agent CLIs are installed (best-effort, no network)."""
    rows = []
    for aid, backend in backends.items():
        if aid == "khavis":
            rows.append({"id": aid, "name": "K.A.V.I.S.", "status": "ready", "note": "LangGraph 7-role"})
            continue
        if aid == "kavis-tencent":
            rows.append({"id": aid, "name": "K.A.V.I.S. (remote)", "status": "config", "note": "adapter"})
            continue
        binary = getattr(backend, "_binary", aid)
        found = shutil.which(binary)
        if not found and aid == "antigravity":
            found = shutil.which("agy")
        rows.append({
            "id": aid,
            "name": type(backend).__name__.replace("Backend", ""),
            "status": "ready" if found else "not_installed",
            "note": found or f"npm/apt install {binary}",
        })
    return rows


async def _run_dispatch(
    prompt: str,
    agents: list[str],
    mode: str = "auto_loop",
    shared_dir: Path | None = None,
    agent_models: dict[str, str] | None = None,
    previous_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one collaborative dispatch in-process."""
    from core.collab_orchestrator import run_collaborative_workflow  # noqa: PLC0415
    from core.workspace_manager import prepare_agent_cwd  # noqa: PLC0415

    backends = _build_backends(agent_models)
    router = DispatchRouter(backends)
    ws = shared_dir or Path.cwd()
    agent_cwds: dict[str, Path] = {}
    for aid in agents:
        if aid == "khavis":
            continue
        with contextlib.suppress(Exception):
            agent_cwds[aid] = prepare_agent_cwd(aid, isolation_mode="shared", project_name="shared-app")

    t0 = time.perf_counter()
    orch = await run_collaborative_workflow(
        router=router,
        backends=backends,
        agents=agents,
        prompt=prompt,
        capability=None,
        shared_dir=ws,
        agent_cwds=agent_cwds,
        collab_mode=mode,
        previous_context=previous_context,
    )
    orch["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return orch


def _print_result(orch: dict[str, Any]) -> None:
    """Pretty-print one dispatch result to the terminal."""
    ok = any(r.get("ok") for r in orch.get("results", []))
    icon = "✅" if ok else "❌"
    mode = orch.get("collab_mode", "?")
    src = orch.get("consensus_source", "?")
    latency = orch.get("latency_ms", 0)
    print(f"\n{icon}  {mode} · {src} · {latency}ms")
    print("─" * 60)

    consensus = str(orch.get("consensus") or "")
    if consensus:
        print(consensus[:3000])
    else:
        for r in orch.get("results", []):
            status = "✓" if r.get("ok") else "✗"
            backend = r.get("backend", "?")
            text = (r.get("text") or r.get("error") or "")[:400]
            print(f"  {status} [{backend}] {text}")
    print("─" * 60)

    files = orch.get("shared_changed_files") or []
    if files:
        print("📁 產出檔案:")
        for f in files[:10]:
            print(f"   {f}")


def _cmd_run(args: argparse.Namespace) -> int:
    load_env(PROJECT_ROOT / ".env")
    prompt = args.prompt
    agents = [a.strip() for a in (args.agents or "khavis").split(",") if a.strip()]
    mode = args.mode

    backends = _build_backends()
    for aid in agents:
        if aid not in backends:
            print(f"❌ 未知 Agent: {aid}", file=sys.stderr)
            _list_agents(backends)
            return 1

    print(f"🚀 {mode} · {' + '.join(agents)}", file=sys.stderr)
    orch = asyncio.run(_run_dispatch(prompt, agents, mode))
    _print_result(orch)
    return 0 if any(r.get("ok") for r in orch.get("results", [])) else 1


def _cmd_agents(args: argparse.Namespace) -> int:
    backends = _build_backends()
    _list_agents(backends)
    return 0


def _list_agents(backends: dict[str, Any]) -> None:
    rows = _available_agents(backends)
    print(f"\n{'ID':<16} {'Backend':<18} {'Status':<14} Note")
    print("─" * 60)
    for r in rows:
        icon = "✅" if r["status"] == "ready" else "○"
        print(f"{icon} {r['id']:<14} {r['name']:<18} {r['status']:<14} {r['note']}")


def _cmd_team(args: argparse.Namespace) -> int:
    """Interactive multi-turn REPL."""
    load_env(PROJECT_ROOT / ".env")
    backends = _build_backends()
    agents = [a.strip() for a in (args.agents or "khavis").split(",") if a.strip()]
    mode = args.mode
    prev_ctx: dict[str, Any] | None = None
    turn = 0

    print("K.H.A.V.I.S. Team — 多 Agent 協同終端機")
    print(f"Agent: {' + '.join(agents)}  模式: {mode}")
    print("輸入任務開始 · /quit 離開 · /agents 查看 · /mode <mode> 切換 · /new 開新對話\n")

    while True:
        try:
            prompt = input("📝 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0
        if not prompt:
            continue
        if prompt in ("/quit", "/exit", "/q"):
            print("再见。")
            return 0
        if prompt == "/agents":
            _list_agents(backends)
            continue
        if prompt == "/new":
            prev_ctx = None
            turn = 0
            print("── 新對話 ──\n")
            continue
        if prompt.startswith("/mode "):
            new_mode = prompt[6:].strip().lower()
            if new_mode in ("auto_loop", "relay", "critique", "parallel"):
                mode = new_mode
                print(f"模式切換為 {mode}\n")
            else:
                print("可用模式: auto_loop / relay / critique / parallel\n")
            continue

        turn += 1
        print(f"\n▶ 第 {turn} 輪 · {mode} · {' + '.join(agents)}")
        orch = asyncio.run(_run_dispatch(prompt, agents, mode, previous_context=prev_ctx))
        _print_result(orch)

        consensus = str(orch.get("consensus") or "")
        prev_ctx = {
            "prompt": prompt,
            "consensus": consensus[:3000],
            "thread": (prev_ctx or {}).get("thread") or f"cli-{int(time.time()*1000)}",
            "step": turn,
        }
        print()


def register_run_subcommands(sub: argparse.ArgumentParser) -> None:
    """Attach run / team / agents subcommands to the existing CLI parser."""
    p_run = sub.add_parser("run", help="one-shot multi-agent collaborative dispatch")
    p_run.add_argument("prompt", help="the task to execute")
    p_run.add_argument("--agents", "-a", default="khavis", help="comma-separated agent IDs (default: khavis)")
    p_run.add_argument("--mode", "-m", default="auto_loop",
                       choices=["auto_loop", "relay", "critique", "parallel"],
                       help="collaboration mode (default: auto_loop)")
    p_run.set_defaults(func=_cmd_run)

    p_team = sub.add_parser("team", help="interactive multi-turn collaborative REPL")
    p_team.add_argument("--agents", "-a", default="khavis", help="comma-separated agent IDs")
    p_team.add_argument("--mode", "-m", default="auto_loop",
                        choices=["auto_loop", "relay", "critique", "parallel"])
    p_team.set_defaults(func=_cmd_team)

    p_ag = sub.add_parser("agents", help="list available agent backends and their status")
    p_ag.set_defaults(func=_cmd_agents)
