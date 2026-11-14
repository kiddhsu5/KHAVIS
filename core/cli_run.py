"""Multi-agent collaborative dispatch for the ``khavis`` CLI.

Bridges ``core/cli.py`` to the multi-agent collaboration orchestrator
(``core/collab_orchestrator.py`` + ``bot/backends/*`` + ``core/custom_agents.py``)
so users can run collaborative agent teams, inspect history, and resume threads
from the terminal — fully synchronized with the Web Workbench.

Subcommands:

    khavis run "task"       one-shot multi-agent collaborative dispatch
    khavis team             interactive multi-turn REPL (Claude Code-style)
    khavis agents           list built-in & custom agent backends and status

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from bot import history as bot_history
from bot.backends import (
    AntigravityBackend,
    ClaudeBackend,
    CodexBackend,
    CommandCodeBackend,
    CustomAgentBackend,
    KavisTencentBackend,
    KhavisBackend,
    MinimaxBackend,
    OpencodeBackend,
)
from bot.backends.kavis_tencent import tencent_endpoint
from bot.dispatch import DispatchRouter

from .cli_ui import (
    MODE_SHORT,
    VALID_MODES,
    C,
    format_agent_chain,
    load_config,
    open_settings,
    print_banner,
    print_status_bar,
    save_config,
    setup_readline,
)
from .custom_agents import load_custom_agents
from .env import load_env

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _ensure_cli_path() -> None:
    """Ensure standard user CLI install locations are on PATH (crucial over non-login SSH)."""
    home = Path.home()
    extra_dirs = [
        home / ".local" / "bin",
        home / ".npm-global" / "bin",
        home / ".minimax-code" / "bin",
        home / ".opencode" / "bin",
        Path("/usr/local/bin"),
    ]
    cur = os.environ.get("PATH", "")
    parts = cur.split(os.pathsep) if cur else []
    changed = False
    for d in extra_dirs:
        s = str(d)
        if d.is_dir() and s not in parts:
            parts.insert(0, s)
            changed = True
    if changed:
        os.environ["PATH"] = os.pathsep.join(parts)


def _new_thread_id() -> str:
    """Generate unified base36 thread ID (`t...`) matching Web Workbench."""
    ms = max(1, int(time.time() * 1000))
    alpha = "0123456789abcdefghijklmnopqrstuvwxyz"
    chars: list[str] = []
    while ms:
        ms, rem = divmod(ms, 36)
        chars.append(alpha[rem])
    return "t" + "".join(reversed(chars))


def _resolve_workspace(
    explicit_cwd: str | None = None, use_shared_ws: bool = False
) -> tuple[Path, str]:
    """Resolve target workspace directory and project name."""
    if explicit_cwd:
        target = Path(explicit_cwd).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target, target.name

    cwd = Path.cwd().resolve()
    if not use_shared_ws and cwd != PROJECT_ROOT.resolve():
        return cwd, cwd.name

    with contextlib.suppress(Exception):
        from core.workspace_manager import (  # noqa: PLC0415
            load_workspace_config,
            prepare_agent_cwd,
        )

        ws_cfg = load_workspace_config()
        proj = str(ws_cfg.get("active_project") or "shared-app")
        shared = prepare_agent_cwd("shared", isolation_mode="shared", project_name=proj)
        return shared, proj

    return cwd, cwd.name


def _build_backends(
    agent_models: dict[str, str] | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    _ensure_cli_path()
    models = agent_models or {}
    cwd_str = str(workspace_dir) if workspace_dir else None

    backends: dict[str, Any] = {
        "claude": ClaudeBackend(
            model=str(models.get("claude") or "").strip(),
            cwd=cwd_str,
        ),
        "codex": CodexBackend(
            model=str(models.get("codex") or "").strip(),
            cwd=cwd_str,
        ),
        "antigravity": AntigravityBackend(
            model=str(models.get("antigravity") or "").strip(),
            cwd=cwd_str,
        ),
        "minimax": MinimaxBackend(
            model=str(models.get("minimax") or "").strip(),
            cwd=cwd_str,
        ),
        "opencode": OpencodeBackend(
            model=str(models.get("opencode") or "").strip(),
            cwd=cwd_str,
        ),
        "cmdcode": CommandCodeBackend(
            model=str(models.get("cmdcode") or "").strip(),
            cwd=cwd_str,
        ),
        "khavis": KhavisBackend(),
        "kavis-tencent": KavisTencentBackend(
            model=str(models.get("kavis-tencent") or "").strip(),
        ),
    }

    for custom_cfg in load_custom_agents():
        cid = str(custom_cfg.get("id") or "").strip()
        if not cid or cid in backends:
            continue
        backends[cid] = CustomAgentBackend(
            custom_cfg,
            model=str(models.get(cid) or custom_cfg.get("default_model") or "").strip(),
            cwd=cwd_str,
        )

    return backends


def _available_agents(backends: dict[str, Any]) -> list[dict[str, str]]:
    _ensure_cli_path()
    rows: list[dict[str, str]] = []
    for aid, backend in backends.items():
        model_str = str(getattr(backend, "model", "") or getattr(backend, "_model", "") or "-")
        if aid == "khavis":
            rows.append(
                {
                    "id": aid,
                    "name": "K.A.V.I.S.",
                    "model": "router",
                    "status": "ready",
                    "note": "LangGraph 7-role in-process",
                }
            )
            continue
        if aid == "kavis-tencent":
            ep = tencent_endpoint()
            rows.append(
                {
                    "id": aid,
                    "name": "KavisTencent",
                    "model": model_str,
                    "status": "ready" if ep else "unconfigured",
                    "note": ep or "set KAVIS_TENCENT_ENDPOINT in .env",
                }
            )
            continue
        if isinstance(backend, CustomAgentBackend):
            spec = getattr(backend, "_spec", {}) or {}
            atype = str(spec.get("type") or "cli").lower()
            binary = str(spec.get("binary") or aid).split()[0]
            found = shutil.which(binary)
            status = "ready" if (atype == "http" or found) else "api_fallback"
            note = (
                f"[自訂 HTTP] {spec.get('base_url') or 'default'}"
                if atype == "http"
                else f"[自訂 CLI] {found or (binary + ' → API fallback')}"
            )
            rows.append(
                {
                    "id": aid,
                    "name": str(spec.get("name") or aid)[:18],
                    "model": model_str,
                    "status": status,
                    "note": note,
                }
            )
            continue

        binary = getattr(backend, "_binary", aid)
        found = shutil.which(binary)
        if not found and aid == "antigravity":
            found = shutil.which("agy")
        if not found and aid == "minimax":
            found = shutil.which("mcode") or shutil.which("minimax")
        rows.append(
            {
                "id": aid,
                "name": type(backend).__name__.replace("Backend", ""),
                "model": model_str,
                "status": "ready" if found else "not_installed",
                "note": found or f"install {binary}",
            }
        )
    return rows


class _Spinner:
    """Lightweight live elapsed-time indicator on stderr for interactive terminals."""

    _FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, label: str, enabled: bool = True) -> None:
        self._label = label
        self._enabled = enabled and sys.stderr.isatty() and not os.environ.get("NO_COLOR")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _Spinner:
        if not self._enabled:
            return self
        t0 = time.perf_counter()

        def _spin() -> None:
            idx = 0
            while not self._stop.wait(0.12):
                elapsed = time.perf_counter() - t0
                frame = self._FRAMES[idx % len(self._FRAMES)]
                idx += 1
                msg = f"\r  {C.cyan(frame)} {self._label} {C.dim(f'({elapsed:.1f}s)')}\033[K"
                sys.stderr.write(msg)
                sys.stderr.flush()

        self._thread = threading.Thread(target=_spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if not self._enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()


async def _run_dispatch(
    prompt: str,
    agents: list[str],
    mode: str = "auto_loop",
    shared_dir: Path | None = None,
    agent_models: dict[str, str] | None = None,
    previous_context: dict[str, Any] | None = None,
    lock_order: bool = True,
) -> dict[str, Any]:
    from core.collab_orchestrator import run_collaborative_workflow  # noqa: PLC0415

    ws = (shared_dir or Path.cwd()).resolve()
    backends = _build_backends(agent_models, workspace_dir=ws)
    router = DispatchRouter(backends)
    agent_cwds: dict[str, Path] = {
        aid: ws for aid in agents if aid not in ("khavis", "kavis-tencent")
    }

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
        agent_order=list(agents),
        lock_order=lock_order,
    )
    orch["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return orch


def _format_file_item(item: Any) -> str:
    """Format a changed/output file entry cleanly (handles dict or str)."""
    if isinstance(item, dict):
        path = str(item.get("path") or item.get("rel_path") or item.get("name") or "")
        status = str(item.get("status") or "").strip().upper()
        size = str(item.get("size") or "").strip()
        badge = f"[{status}] " if status else ""
        sz = f" ({size})" if size else ""
        return f"{badge}{path}{sz}"
    return str(item)


def _record_cli_history(
    *,
    prompt: str,
    agents: list[str],
    orch: dict[str, Any],
    project_name: str,
    thread_id: str,
    step: int,
) -> None:
    """Record CLI dispatch to shared dispatches.jsonl so Web & CLI share history."""
    raw_files = orch.get("shared_changed_files") or []
    out_files = []
    for f in raw_files[:25]:
        if isinstance(f, dict) and f.get("path"):
            out_files.append(f)
        elif isinstance(f, str):
            out_files.append({"path": f, "status": "M"})

    bot_history.record(
        {
            "ts": time.time(),
            "kind": "workbench",
            "source": "cli",
            "prompt": prompt,
            "backends": list(agents),
            "collab_mode": orch.get("collab_mode", "auto_loop"),
            "consensus_source": orch.get("consensus_source") or ", ".join(agents),
            "project": project_name,
            "isolation_mode": "shared",
            "thread": thread_id,
            "step": step,
            "consensus": str(orch.get("consensus") or ""),
            "review_details": str(orch.get("review_details") or ""),
            "output_files": out_files,
            "dispatched_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "ok": any(r.get("ok") for r in orch.get("results", [])),
            "latency_ms": int(orch.get("latency_ms") or 0),
        }
    )


def _recent_threads(limit: int = 12) -> list[dict[str, Any]]:
    """Group recent history rows into conversation threads (newest first)."""
    rows = bot_history.tail(150)
    by_tid: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        tid = str(row.get("thread") or "").strip()
        if not tid:
            continue
        step = int(row.get("step") or 1)
        if tid not in by_tid:
            by_tid[tid] = {
                "thread": tid,
                "max_step": step,
                "turns": 1,
                "latest": row,
            }
            order.append(tid)
        else:
            entry = by_tid[tid]
            entry["turns"] += 1
            if step >= entry["max_step"]:
                entry["max_step"] = max(entry["max_step"], step)
    return [by_tid[t] for t in order[:limit]]


def _resolve_resume_context(target: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve 'latest', 1-based index, or thread ID into previous_context + metadata."""
    threads = _recent_threads(30)
    if not threads:
        return None, None

    chosen: dict[str, Any] | None = None
    key = target.strip()
    if not key or key == "latest":
        chosen = threads[0]
    elif key.isdigit() and 1 <= int(key) <= len(threads):
        chosen = threads[int(key) - 1]
    else:
        for t in threads:
            if t["thread"] == key or t["thread"].endswith(key):
                chosen = t
                break
    if not chosen:
        return None, None

    row = chosen["latest"]
    ctx = {
        "prompt": str(row.get("prompt") or ""),
        "consensus": str(row.get("consensus") or row.get("result") or "")[:4000],
        "thread": chosen["thread"],
        "step": int(chosen["max_step"]),
    }
    return ctx, chosen


def _print_result(
    orch: dict[str, Any],
    *,
    max_lines: int = 80,
    show_timing: bool = True,
    thread_id: str | None = None,
    step: int | None = None,
    show_next_actions: bool = False,
) -> None:
    results = orch.get("results") or []
    ok = any(r.get("ok") for r in results)
    icon = C.green("✅") if ok else C.red("❌")
    mode = str(orch.get("collab_mode") or "?")
    mode_zh = MODE_SHORT.get(mode, mode)
    src = str(orch.get("consensus_source") or "?")
    ms = int(orch.get("latency_ms") or 0)
    timing = (
        f" · {ms / 1000:.1f}s"
        if (show_timing and ms >= 1000)
        else (f" · {ms}ms" if show_timing else "")
    )
    t_meta = f" · #{thread_id}" + (f" (第 {step} 輪)" if step else "") if thread_id else ""

    print(
        f"\n{icon}  {C.bold(mode)} {C.dim('(' + mode_zh + ')')} · "
        f"{C.cyan(src)}{C.dim(timing + t_meta)}"
    )

    steps = orch.get("collaboration_steps") or []
    if steps:
        trace_parts = []
        for s in steps:
            s_ok = C.green("✓") if s.get("ok") else C.red("✗")
            ag = str(s.get("agent") or "?")
            lat = s.get("latency_ms")
            lat_s = f"({lat / 1000:.1f}s)" if isinstance(lat, (int, float)) and lat >= 100 else ""
            trace_parts.append(f"{s_ok} {ag}{C.dim(lat_s)}")
        sep = " → " if mode in ("relay", "preempt") else " · "
        print(f"   {C.dim('階段軌跡：')}{sep.join(trace_parts)}")

    print(C.dim("─" * 64))

    consensus = str(orch.get("consensus") or "").strip()
    if consensus:
        lines = consensus.split("\n")
        if max_lines and max_lines > 0 and len(lines) > max_lines:
            print("\n".join(lines[:max_lines]))
            hint = "輸入 /full 檢視完整內容" if show_next_actions else "加上 --full 檢視完整內容"
            print(C.dim(f"\n…（共 {len(lines)} 行，目前顯示前 {max_lines} 行；可{hint}）"))
        else:
            print(consensus)
    else:
        for r in results:
            status = C.green("✓") if r.get("ok") else C.red("✗")
            backend = r.get("backend", "?")
            text = (r.get("text") or r.get("error") or "")[:500]
            print(f"  {status} {C.bold('[' + str(backend) + ']')} {text}")
    print(C.dim("─" * 64))

    files = orch.get("shared_changed_files") or []
    if files:
        print(f"  {C.bold('📁 本輪變更／產出檔案：')}")
        for f in files[:12]:
            print(f"     {C.cyan(_format_file_item(f))}")
        if len(files) > 12:
            print(f"     {C.dim(f'…及其他 {len(files) - 12} 個檔案')}")

    if show_next_actions:
        actions = orch.get("next_actions") or []
        if actions:
            print(
                f"  {C.dim('💡 建議後續行動（輸入 /1 ～ /' + str(min(4, len(actions))) + ' 一鍵執行）：')}"
            )
            for idx, act in enumerate(actions[:4], 1):
                label = str(act.get("label") or "")
                amode = str(act.get("mode") or mode)
                print(f"     {C.yellow(f'/{idx}')} {label} {C.dim(f'[{amode}]')}")


def _print_steps_detail(orch: dict[str, Any] | None) -> None:
    if not orch:
        print(f"  {C.dim('尚無上一輪執行紀錄。')}\n")
        return
    print(f"\n  {C.bold('🔍 上一輪執行明細')}")
    critique = str(orch.get("critique_summary") or "").strip()
    if critique:
        print(f"  {C.yellow('摘要：')}{critique}")
    for i, s in enumerate(orch.get("collaboration_steps") or [], 1):
        mark = C.green("✓") if s.get("ok") else C.red("✗")
        stage = s.get("stage") or f"Step {i}"
        role = s.get("role") or ""
        lat = s.get("latency_ms") or 0
        print(f"\n  {mark} {C.bold(str(stage))} {C.dim(f'({role}, {lat}ms)')}")
        summary = str(s.get("summary") or "").strip()
        if summary:
            print(f"    {C.dim(summary[:400])}")
    rev = str(orch.get("review_details") or "").strip()
    if rev:
        print(f"\n  {C.bold('📋 交叉審查紀錄：')}\n{rev[:2500]}")
    print()


def _print_history_list(limit: int = 10) -> None:
    threads = _recent_threads(limit)
    if not threads:
        print(f"\n  {C.dim('目前尚無歷史對話串。')}\n")
        return
    print(f"\n  {C.bold('🕓 最近對話串（與 Web 工作台共用）')}")
    print(f"  {C.dim('─' * 64)}")
    for idx, item in enumerate(threads, 1):
        row = item["latest"]
        tid = item["thread"]
        step = item["max_step"]
        when = str(row.get("dispatched_at") or "")[-14:]
        mode = str(row.get("collab_mode") or "auto_loop")
        bks = ", ".join(str(x) for x in (row.get("backends") or []))
        prompt_preview = str(row.get("prompt") or "").replace("\n", " ").strip()[:42]
        ok_mark = C.green("✓") if row.get("ok", True) else C.red("✗")
        print(
            f"  {C.yellow(str(idx).rjust(2) + '.')} {ok_mark} {C.cyan(tid.ljust(11))} "
            f"{C.dim(f'共{step}輪 · {when}')}  {C.dim(f'[{mode}: {bks}]')}"
        )
        print(f"      {prompt_preview}")
    print(f"  {C.dim('─' * 64)}")
    print(f"  {C.dim('提示：輸入 /resume <編號或thread_id> 即可接續該對話串。')}\n")


def _parse_model_overrides(raw_pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in raw_pairs or []:
        if "=" in item:
            k, v = item.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


def _cmd_run(args: argparse.Namespace) -> int:
    load_env(PROJECT_ROOT / ".env")
    cfg = load_config()

    prompt = (args.prompt or "").strip()
    if prompt == "-" or (not prompt and not sys.stdin.isatty()):
        prompt = sys.stdin.read().strip()
    if not prompt:
        err_msg = '❌ 請提供任務指令（例如：khavis run "檢查專案架構"）'
        print(C.red(err_msg), file=sys.stderr)
        return 1

    if args.agents is not None:
        parsed = [a.strip() for a in args.agents.split(",") if a.strip()]
        if parsed:
            cfg["agents"] = parsed
    if args.mode is not None:
        cfg["mode"] = args.mode

    models = dict(cfg.get("agent_models") or {})
    models.update(_parse_model_overrides(getattr(args, "model", None)))

    agents: list[str] = cfg["agents"]
    mode: str = cfg["mode"]
    ws, proj_name = _resolve_workspace(
        getattr(args, "cwd", None), getattr(args, "workspace", False)
    )

    backends = _build_backends(models, workspace_dir=ws)
    for aid in agents:
        if aid not in backends:
            print(f"{C.red('❌ 未知 Agent: ' + aid)}", file=sys.stderr)
            _list_agents(backends)
            return 1

    prev_ctx: dict[str, Any] | None = None
    resume_arg = getattr(args, "resume_thread", None)
    if resume_arg is not None:
        prev_ctx, meta = _resolve_resume_context(resume_arg)
        if not prev_ctx:
            print(f"{C.red(f'❌ 找不到可接續的對話串：{resume_arg}')}", file=sys.stderr)
            return 1
        if not getattr(args, "json", False) and meta:
            resumed_tid = prev_ctx["thread"]
            resumed_step = meta["max_step"]
            print(
                C.dim(f"↳ 接續對話串 #{resumed_tid}（已完成 {resumed_step} 輪）"),
                file=sys.stderr,
            )

    thread_id = (prev_ctx or {}).get("thread") or _new_thread_id()
    step = int((prev_ctx or {}).get("step") or 0) + 1
    chain_label = format_agent_chain(agents, mode, models)

    if not getattr(args, "json", False):
        print(
            f"{C.cyan('🚀')} {C.bold(mode)} ({MODE_SHORT.get(mode, mode)}) · {chain_label} · {C.dim(str(ws))}",
            file=sys.stderr,
        )

    with _Spinner(f"執行中 · {mode} · {chain_label}", enabled=not getattr(args, "json", False)):
        orch = asyncio.run(
            _run_dispatch(
                prompt,
                agents,
                mode,
                shared_dir=ws,
                agent_models=models,
                previous_context=prev_ctx,
            )
        )

    _record_cli_history(
        prompt=prompt,
        agents=agents,
        orch=orch,
        project_name=proj_name,
        thread_id=thread_id,
        step=step,
    )

    if getattr(args, "json", False):
        payload = {
            "ok": any(r.get("ok") for r in orch.get("results", [])),
            "thread": thread_id,
            "step": step,
            "workspace": str(ws),
            **orch,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        max_lines = 0 if getattr(args, "full", False) else int(cfg.get("max_output_lines", 80))
        _print_result(
            orch,
            max_lines=max_lines,
            show_timing=bool(cfg.get("show_timing", True)),
            thread_id=thread_id,
            step=step,
        )
    return 0 if any(r.get("ok") for r in orch.get("results", [])) else 1


def _cmd_agents(args: argparse.Namespace) -> int:
    load_env(PROJECT_ROOT / ".env")
    cfg = load_config()
    backends = _build_backends(cfg.get("agent_models"))
    if getattr(args, "json", False):
        print(json.dumps(_available_agents(backends), ensure_ascii=False, indent=2))
        return 0
    _list_agents(backends)
    return 0


def _list_agents(backends: dict[str, Any]) -> None:
    rows = _available_agents(backends)
    h_id = C.bold("ID".ljust(16))
    h_name = C.bold("Backend".ljust(16))
    h_model = C.bold("Model".ljust(22))
    h_status = C.bold("Status".ljust(15))
    print(f"\n  {h_id}{h_name}{h_model}{h_status}Note")
    print(f"  {C.dim('─' * 82)}")
    for r in rows:
        is_ready = r["status"] in ("ready", "api_fallback")
        icon = (
            C.green("✅")
            if r["status"] == "ready"
            else (C.yellow("⚡") if is_ready else C.dim("○ "))
        )
        aid = C.cyan(r["id"].ljust(14))
        name = r["name"][:15].ljust(16)
        model = r.get("model", "-")[:20].ljust(22)
        stat = r["status"].ljust(15)
        print(f"  {icon} {aid} {name} {C.dim(model)} {stat} {C.dim(r['note'])}")
    print()


def _print_team_help() -> None:
    print(
        f"""
  {C.bold("── K.H.A.V.I.S. 終端機互動指令一覽 ────────────────────────")}
  {C.yellow("/config")}            開啟互動式設定選單（Agent 順序、模式、模型、主題、行數）
  {C.yellow("/agents")}            列出所有內建與自訂 Agent 的安裝與模型狀態
  {C.yellow("/agents a,b")}        切換參與的 Agent（例：{C.cyan("/agents claude,codex")}）
  {C.yellow("/order a,b")}         設定接力／搶佔交接順序（例：{C.cyan("/order minimax,claude")}）
  {C.yellow("/mode <模式>")}       切換模式（{" / ".join(VALID_MODES)}）
  {C.yellow("/model [a=m]")}       檢視或覆蓋模型（例：{C.cyan("/model claude=claude-sonnet-4-5")}；清除：{C.cyan("/model claude=-")}）
  {C.yellow("/history")}           列出最近對話串（與 Web 工作台雙向同步）
  {C.yellow("/resume <編號|id>")}  從指定歷史對話串接續對話（例：{C.cyan("/resume 1")}）
  {C.yellow("/full")}              展開顯示上一輪完整回覆（不截斷行數）
  {C.yellow("/steps")}             檢視上一輪各階段耗時、角色軌跡與交叉審查明細
  {C.yellow("/1")} ～ {C.yellow("/4")}           一鍵執行上一輪結果建議的後續行動
  {C.yellow("/new")}               開啟全新對話串
  {C.yellow("/save")}              將目前團隊、模式與模型儲存為預設值
  {C.yellow("/quit")}              離開互動模式
"""
    )


def _cmd_team(args: argparse.Namespace) -> int:
    """Interactive multi-turn REPL with Claude Code-style welcome + settings."""
    load_env(PROJECT_ROOT / ".env")
    cfg = load_config()

    if args.agents is not None:
        parsed = [a.strip() for a in args.agents.split(",") if a.strip()]
        if parsed:
            cfg["agents"] = parsed
    if args.mode is not None:
        cfg["mode"] = args.mode
    if getattr(args, "no_banner", False):
        cfg["show_banner"] = False
    cfg.setdefault("agent_models", {}).update(_parse_model_overrides(getattr(args, "model", None)))

    ws, proj_name = _resolve_workspace(
        getattr(args, "cwd", None), getattr(args, "workspace", False)
    )
    backends = _build_backends(cfg.get("agent_models"), workspace_dir=ws)
    known_ids = list(backends.keys())
    setup_readline(known_ids)

    agents: list[str] = cfg["agents"]
    mode: str = cfg["mode"]
    prev_ctx: dict[str, Any] | None = None
    last_orch: dict[str, Any] | None = None
    turn = 0
    max_lines = int(cfg.get("max_output_lines", 80))
    show_timing = bool(cfg.get("show_timing", True))

    resume_arg = getattr(args, "resume_thread", None)
    if resume_arg is not None:
        prev_ctx, _ = _resolve_resume_context(resume_arg)
        if prev_ctx:
            turn = int(prev_ctx.get("step") or 0)

    agent_rows = _available_agents(backends)
    print_banner(cfg, agent_rows, workspace=ws)
    if prev_ctx:
        print(
            f"  {C.green('↳ 已載入歷史對話串')} {C.cyan('#' + str(prev_ctx['thread']))} "
            f"{C.dim(f'（已完成 {turn} 輪）')}\n"
        )

    while True:
        print_status_bar(cfg, turn, (prev_ctx or {}).get("thread"))
        try:
            prompt = input(f"  {C.magenta('▸')} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {C.dim('再見！')}")
            return 0

        if not prompt:
            continue

        # ── Slash commands ─────────────────────────────────────
        if prompt in ("/quit", "/exit", "/q"):
            print(f"  {C.dim('再見！')}")
            return 0

        if prompt in ("/config", "/settings"):
            cfg = open_settings(cfg, available_ids=known_ids)
            agents = cfg["agents"]
            mode = cfg["mode"]
            max_lines = int(cfg.get("max_output_lines", 80))
            show_timing = bool(cfg.get("show_timing", True))
            backends = _build_backends(cfg.get("agent_models"), workspace_dir=ws)
            continue

        if prompt == "/agents":
            backends = _build_backends(cfg.get("agent_models"), workspace_dir=ws)
            _list_agents(backends)
            continue

        if prompt.startswith(("/agents ", "/order ")):
            is_order = prompt.startswith("/order ")
            raw_part = prompt.split(None, 1)[1] if len(prompt.split(None, 1)) > 1 else ""
            ids = [a.strip() for a in raw_part.split(",") if a.strip()]
            unknown = [a for a in ids if a not in backends]
            if unknown:
                print(
                    f"  {C.red('未知 Agent：' + ', '.join(unknown))} "
                    f"{C.dim('（輸入 /agents 查看可用代號）')}\n"
                )
            elif ids:
                agents = ids
                cfg["agents"] = ids
                chain = format_agent_chain(agents, mode, cfg.get("agent_models"))
                note = ""
                if is_order and mode not in ("relay", "preempt") and len(ids) >= 2:
                    note = f" {C.dim('（提示：搭配 /mode relay 或 /mode preempt 可啟用嚴格順序交接）')}"
                print(f"  {C.green('✅ 團隊順序 → ' + chain)}{note}\n")
            continue

        if prompt.startswith("/mode"):
            parts = prompt.split(None, 1)
            if len(parts) > 1 and parts[1].strip().lower() in VALID_MODES:
                mode = parts[1].strip().lower()
                cfg["mode"] = mode
                print(
                    f"  {C.green('✅ 模式 → ' + mode)} "
                    f"{C.dim('(' + MODE_SHORT.get(mode, mode) + ')')}\n"
                )
            else:
                opts = " / ".join(f"{m}({MODE_SHORT[m]})" for m in VALID_MODES)
                print(f"  {C.dim('可用模式：' + opts)}\n")
            continue

        if prompt.startswith("/model"):
            parts = prompt.split(None, 1)
            models = cfg.setdefault("agent_models", {})
            if len(parts) == 1:
                if not models:
                    print(
                        f"  {C.dim('目前所有 Agent 均使用預設模型。用法：/model claude=claude-sonnet-4-5')}\n"
                    )
                else:
                    for k, v in models.items():
                        print(f"  {C.cyan(k.ljust(14))} → {C.yellow(v)}")
                    print()
            else:
                arg = parts[1].strip()
                if arg == "clear":
                    models.clear()
                    print(f"  {C.green('✅ 已清除所有自訂模型覆蓋')}\n")
                elif "=" in arg:
                    k, v = [x.strip() for x in arg.split("=", 1)]
                    if k not in backends:
                        print(f"  {C.red('未知 Agent：' + k)}\n")
                    elif v in ("", "-"):
                        models.pop(k, None)
                        print(f"  {C.green(f'✅ 已還原 {k} 為預設模型')}\n")
                    else:
                        models[k] = v
                        print(f"  {C.green(f'✅ {k} 模型 → {v}')}\n")
                else:
                    print(
                        f"  {C.dim('用法：/model <agent>=<model> 或 /model <agent>=- 還原預設')}\n"
                    )
                backends = _build_backends(models, workspace_dir=ws)
            continue

        if prompt == "/history":
            _print_history_list()
            continue

        if prompt.startswith("/resume"):
            parts = prompt.split(None, 1)
            target = parts[1].strip() if len(parts) > 1 else "latest"
            ctx, meta = _resolve_resume_context(target)
            if not ctx or not meta:
                print(f"  {C.red('找不到指定的歷史對話串，請先輸入 /history 查看。')}\n")
            else:
                prev_ctx = ctx
                turn = int(ctx["step"])
                last_p = str(ctx["prompt"])[:36]
                print(
                    f"  {C.green('✅ 已接續對話串')} {C.cyan('#' + ctx['thread'])} "
                    f"{C.dim(f'（第 {turn} 輪，上輪指令：{last_p}）')}\n"
                )
            continue

        if prompt == "/full":
            if not last_orch:
                print(f"  {C.dim('尚無上一輪輸出可展開。')}\n")
            else:
                _print_result(
                    last_orch,
                    max_lines=0,
                    show_timing=show_timing,
                    thread_id=(prev_ctx or {}).get("thread"),
                    step=turn,
                )
                print()
            continue

        if prompt == "/steps":
            _print_steps_detail(last_orch)
            continue

        if prompt == "/new":
            prev_ctx = None
            last_orch = None
            turn = 0
            print(f"  {C.dim('── 已開啟新對話串 ──')}\n")
            continue

        if prompt == "/save":
            save_config(cfg)
            print(f"  {C.green('✅ 設定已儲存')}\n")
            continue

        if prompt == "/help":
            _print_team_help()
            continue

        # One-click follow-up actions (/1 .. /4)
        if prompt in ("/1", "/2", "/3", "/4"):
            actions = (last_orch or {}).get("next_actions") or []
            idx = int(prompt[1:]) - 1
            if 0 <= idx < len(actions):
                act = actions[idx]
                prompt = str(act.get("prompt") or "").strip()
                act_mode = str(act.get("mode") or "").strip().lower()
                if act_mode in VALID_MODES:
                    mode = act_mode
                    cfg["mode"] = mode
                print(f"  {C.yellow('⚡ 執行建議行動：')}{act.get('label', '')}")
            else:
                print(f"  {C.dim('目前沒有對應的建議行動編號。')}\n")
                continue

        if prompt.startswith("/run"):
            rest = prompt[4:].strip()
            if not rest:
                print(f"  {C.dim('用法：直接輸入任務內容，或 /run <任務內容>')}\n")
                continue
            prompt = rest
        elif prompt.startswith("/"):
            print(
                f"  {C.red('未知指令：' + prompt.split()[0])} {C.dim('（輸入 /help 查看完整指令清單）')}\n"
            )
            continue

        # ── Dispatch ───────────────────────────────────────────
        turn += 1
        thread_id = (prev_ctx or {}).get("thread") or _new_thread_id()
        chain_label = format_agent_chain(agents, mode, cfg.get("agent_models"))
        print(
            f"\n  {C.cyan('▶')} 第 {turn} 輪 · {mode} ({MODE_SHORT.get(mode, mode)}) · {chain_label}"
        )
        try:
            with _Spinner(f"執行中 · {mode} · {chain_label}"):
                orch = asyncio.run(
                    _run_dispatch(
                        prompt,
                        agents,
                        mode,
                        shared_dir=ws,
                        agent_models=cfg.get("agent_models"),
                        previous_context=prev_ctx,
                    )
                )
        except KeyboardInterrupt:
            turn -= 1
            print(f"\n  {C.yellow('⚠ 已中斷本輪執行。')}\n")
            continue
        except Exception as exc:  # noqa: BLE001
            turn -= 1
            print(f"  {C.red('❌ ' + str(exc)[:240])}\n")
            continue

        last_orch = orch
        _record_cli_history(
            prompt=prompt,
            agents=agents,
            orch=orch,
            project_name=proj_name,
            thread_id=thread_id,
            step=turn,
        )
        _print_result(
            orch,
            max_lines=max_lines,
            show_timing=show_timing,
            thread_id=thread_id,
            step=turn,
            show_next_actions=True,
        )

        consensus = str(orch.get("consensus") or "")
        prev_ctx = {
            "prompt": prompt,
            "consensus": consensus[:4000],
            "thread": thread_id,
            "step": turn,
        }
        print()


def register_run_subcommands(sub: Any) -> None:
    p_run = sub.add_parser(
        "run",
        help="單次多 Agent 協同任務派發（支援接力、搶佔、互評、自動閉環）",
    )
    p_run.add_argument(
        "prompt",
        nargs="?",
        default="",
        help="要執行的任務指令（亦可透過管線 stdin 傳入或填 '-'）",
    )
    p_run.add_argument(
        "--agents",
        "-a",
        default=None,
        help="以逗號分隔的 Agent 代號與順序（預設讀取 ~/.khavis/cli_config.json）",
    )
    p_run.add_argument(
        "--mode",
        "-m",
        default=None,
        choices=VALID_MODES,
        help="協同模式：auto_loop / relay / preempt / critique / parallel",
    )
    p_run.add_argument(
        "--model",
        action="append",
        default=None,
        metavar="AGENT=MODEL",
        help="指定個別 Agent 模型（可重複指定，例：--model claude=claude-sonnet-4-5）",
    )
    p_run.add_argument(
        "--cwd",
        "-C",
        default=None,
        help="指定執行目標工作區目錄",
    )
    p_run.add_argument(
        "--workspace",
        action="store_true",
        help="強制使用 KHAVIS 共用專案工作區（與 Web 工作台相同目錄）",
    )
    p_run.add_argument(
        "--continue",
        "-c",
        dest="resume_thread",
        nargs="?",
        const="latest",
        default=None,
        metavar="THREAD",
        help="接續上一輪對話串（或指定 thread ID / 編號）",
    )
    p_run.add_argument(
        "--full",
        action="store_true",
        help="完整輸出回覆內容（不依行數上限截斷）",
    )
    p_run.add_argument(
        "--json",
        action="store_true",
        help="輸出結構化 JSON（適合自動化腳本串接）",
    )
    p_run.set_defaults(func=_cmd_run)

    p_team = sub.add_parser(
        "team",
        help="啟動互動式多輪協同終端機（支援歷史接續、設定選單與 Tab 補全）",
    )
    p_team.add_argument("--agents", "-a", default=None, help="以逗號分隔的 Agent 代號與順序")
    p_team.add_argument("--mode", "-m", default=None, choices=VALID_MODES, help="預設協同模式")
    p_team.add_argument(
        "--model",
        action="append",
        default=None,
        metavar="AGENT=MODEL",
        help="指定個別 Agent 模型",
    )
    p_team.add_argument("--cwd", "-C", default=None, help="指定執行目標工作區目錄")
    p_team.add_argument(
        "--workspace",
        action="store_true",
        help="強制使用 KHAVIS 共用專案工作區",
    )
    p_team.add_argument(
        "--continue",
        "-c",
        dest="resume_thread",
        nargs="?",
        const="latest",
        default=None,
        metavar="THREAD",
        help="啟動時直接載入最近一輪或指定歷史對話串",
    )
    p_team.add_argument("--no-banner", action="store_true", help="略過啟動歡迎畫面")
    p_team.set_defaults(func=_cmd_team)

    p_ag = sub.add_parser(
        "agents",
        help="列出所有內建與自訂 Agent CLI／服務商的就緒狀態",
    )
    p_ag.add_argument("--json", action="store_true", help="以 JSON 陣列輸出 Agent 狀態")
    p_ag.set_defaults(func=_cmd_agents)
