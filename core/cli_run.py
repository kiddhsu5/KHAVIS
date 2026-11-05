"""Multi-agent collaborative dispatch for the ``khavis`` CLI.

Bridges the existing CLI (``core/cli.py``) to the multi-agent collaboration
layer (``core/collab_orchestrator.py`` + ``bot/backends/*``) so users can run
collaborative agent teams entirely from the terminal, without the Web
Workbench.

Subcommands:

    khavis run "task"                       one-shot multi-agent dispatch
    khavis team                             interactive REPL (Claude Code-style)

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

from .cli_ui import (
    C,
    ALL_AGENT_IDS,
    VALID_MODES,
    load_config,
    save_config,
    print_banner,
    print_status_bar,
    open_settings,
)
from .env import load_env

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _build_backends(agent_models: dict[str, str] | None = None) -> dict[BackendName, Any]:
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
    rows = []
    for aid, backend in backends.items():
        if aid == "khavis":
            rows.append({"id": aid, "name": "K.A.V.I.S.", "status": "ready", "note": "LangGraph 7-role"})
            continue
        binary = getattr(backend, "_binary", aid)
        found = shutil.which(binary)
        if not found and aid == "antigravity":
            found = shutil.which("agy")
        rows.append({
            "id": aid,
            "name": type(backend).__name__.replace("Backend", ""),
            "status": "ready" if found else "not_installed",
            "note": found or f"install {binary}",
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


def _print_result(orch: dict[str, Any], max_lines: int = 50, show_timing: bool = True) -> None:
    ok = any(r.get("ok") for r in orch.get("results", []))
    icon = C.green("✅") if ok else C.red("❌")
    mode = orch.get("collab_mode", "?")
    src = orch.get("consensus_source", "?")
    timing = f" · {orch['latency_ms']}ms" if show_timing else ""
    print(f"\n{icon}  {C.bold(mode)} · {C.dim(src)}{C.dim(timing)}")
    print(C.dim("─" * 60))

    consensus = str(orch.get("consensus") or "")
    if consensus:
        lines = consensus.split("\n")
        shown = lines[:max_lines]
        print("\n".join(shown))
        if len(lines) > max_lines:
            print(C.dim(f"…（共 {len(lines)} 行，顯示前 {max_lines} 行）"))
    else:
        for r in orch.get("results", []):
            status = C.green("✓") if r.get("ok") else C.red("✗")
            backend = r.get("backend", "?")
            text = (r.get("text") or r.get("error") or "")[:400]
            print(f"  {status} {C.bold('[' + backend + ']')} {text}")
    print(C.dim("─" * 60))

    files = orch.get("shared_changed_files") or []
    if files:
        print(f"{C.bold('📁 產出檔案:')}")
        for f in files[:10]:
            print(f"   {C.cyan(str(f))}")


def _cmd_run(args: argparse.Namespace) -> int:
    load_env(PROJECT_ROOT / ".env")
    cfg = load_config()
    if args.agents != "khavis":
        cfg["agents"] = [a.strip() for a in args.agents.split(",") if a.strip()]
    if args.mode != "auto_loop":
        cfg["mode"] = args.mode
    agents = cfg["agents"]
    mode = cfg["mode"]

    backends = _build_backends()
    for aid in agents:
        if aid not in backends:
            print(f"{C.red('❌ 未知 Agent: ' + aid)}", file=sys.stderr)
            _list_agents(backends)
            return 1

    print(f"{C.cyan('🚀')} {mode} · {' + '.join(agents)}", file=sys.stderr)
    orch = asyncio.run(_run_dispatch(args.prompt, agents, mode))
    _print_result(orch, max_lines=cfg.get("max_output_lines", 50), show_timing=cfg.get("show_timing", True))
    return 0 if any(r.get("ok") for r in orch.get("results", [])) else 1


def _cmd_agents(args: argparse.Namespace) -> int:
    backends = _build_backends()
    _list_agents(backends)
    return 0


def _list_agents(backends: dict[str, Any]) -> None:
    rows = _available_agents(backends)
    header_id = C.bold("ID".ljust(16))
    header_name = C.bold("Backend".ljust(18))
    header_status = C.bold("Status".ljust(16))
    print(f"\n  {header_id}{header_name}{header_status}Note")
    print(f"  {C.dim('─' * 60)}")
    for r in rows:
        is_ready = r["status"] == "ready"
        icon = C.green("✅") if is_ready else C.dim("○")
        aid = C.cyan(r["id"].ljust(14))
        name = r["name"].ljust(18)
        stat = r["status"].ljust(16)
        print(f"  {icon} {aid} {name} {stat} {C.dim(r['note'])}")


def _cmd_team(args: argparse.Namespace) -> int:
    """Interactive multi-turn REPL with Claude Code-style welcome + settings."""
    load_env(PROJECT_ROOT / ".env")
    backends = _build_backends()
    cfg = load_config()

    # CLI flags override persisted config
    if args.agents != "khavis":
        cfg["agents"] = [a.strip() for a in args.agents.split(",") if a.strip()]
    if args.mode != "auto_loop":
        cfg["mode"] = args.mode

    agents = cfg["agents"]
    mode = cfg["mode"]
    prev_ctx: dict[str, Any] | None = None
    turn = 0
    max_lines = cfg.get("max_output_lines", 50)
    show_timing = cfg.get("show_timing", True)

    agent_rows = _available_agents(backends)
    print_banner(cfg, agent_rows)

    while True:
        print_status_bar(cfg, turn)
        try:
            prompt = input(f"  {C.magenta('▸')} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {C.dim('再见。')}")
            return 0

        if not prompt:
            continue

        # ── Slash commands ─────────────────────────────────────
        if prompt in ("/quit", "/exit", "/q"):
            print(f"  {C.dim('再见。')}")
            return 0
        if prompt in ("/config", "/settings"):
            cfg = open_settings(cfg)
            agents = cfg["agents"]
            mode = cfg["mode"]
            max_lines = cfg.get("max_output_lines", 50)
            show_timing = cfg.get("show_timing", True)
            continue
        if prompt == "/agents":
            _list_agents(backends)
            continue
        if prompt == "/new":
            prev_ctx = None
            turn = 0
            print(f"  {C.dim('── 新對話 ──')}\n")
            continue
        if prompt == "/save":
            save_config(cfg)
            print(f"  {C.green('✅ 設定已儲存')}\n")
            continue
        if prompt.startswith("/mode"):
            parts = prompt.split(None, 1)
            if len(parts) > 1 and parts[1].strip().lower() in VALID_MODES:
                mode = parts[1].strip().lower()
                cfg["mode"] = mode
                print(f"  {C.green('模式 → ' + mode)}\n")
            else:
                print(f"  {C.dim('可用: ' + ' / '.join(VALID_MODES))}\n")
            continue
        if prompt.startswith("/agents "):
            ids = [a.strip() for a in prompt[8:].split(",") if a.strip() and a.strip() in ALL_AGENT_IDS]
            if ids:
                agents = ids
                cfg["agents"] = ids
                print(f"  {C.green('Agent → ' + ', '.join(ids))}\n")
            continue
        if prompt == "/help":
            print(f"""
  {C.yellow('/config')}    開啟設定選單（Agent、模式、顯示選項）
  {C.yellow('/agents')}    列出 Agent 狀態
  {C.yellow('/agents A,B')} 切換 Agent
  {C.yellow('/mode M')}    切換模式（{'/'.join(VALID_MODES)}）
  {C.yellow('/new')}       開始新對話
  {C.yellow('/save')}      儲存目前設定
  {C.yellow('/quit')}      離開
""")
            continue

        # ── Dispatch ───────────────────────────────────────────
        turn += 1
        print(f"\n  {C.cyan('▶')} 第 {turn} 輪 · {mode} · {' + '.join(agents)}")
        try:
            orch = asyncio.run(_run_dispatch(prompt, agents, mode, previous_context=prev_ctx))
        except Exception as exc:  # noqa: BLE001
            print(f"  {C.red('❌ ' + str(exc)[:200])}\n")
            continue
        _print_result(orch, max_lines=max_lines, show_timing=show_timing)

        consensus = str(orch.get("consensus") or "")
        prev_ctx = {
            "prompt": prompt,
            "consensus": consensus[:3000],
            "thread": (prev_ctx or {}).get("thread") or f"cli-{int(time.time()*1000)}",
            "step": turn,
        }
        print()


def register_run_subcommands(sub: argparse.ArgumentParser) -> None:
    p_run = sub.add_parser("run", help="one-shot multi-agent collaborative dispatch")
    p_run.add_argument("prompt", help="the task to execute")
    p_run.add_argument("--agents", "-a", default="khavis", help="comma-separated agent IDs (default from config)")
    p_run.add_argument("--mode", "-m", default="auto_loop",
                       choices=VALID_MODES, help="collaboration mode (default from config)")
    p_run.set_defaults(func=_cmd_run)

    p_team = sub.add_parser("team", help="interactive multi-turn REPL (Claude Code-style welcome)")
    p_team.add_argument("--agents", "-a", default="khavis", help="comma-separated agent IDs")
    p_team.add_argument("--mode", "-m", default="auto_loop", choices=VALID_MODES)
    p_team.set_defaults(func=_cmd_team)

    p_ag = sub.add_parser("agents", help="list available agent backends and their status")
    p_ag.set_defaults(func=_cmd_agents)


import argparse  # noqa: E402  (needed for type hint in register function)
