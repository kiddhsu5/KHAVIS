"""Rich terminal UI for the ``khavis team`` CLI (Claude Code-style welcome + settings).

Provides:
- ASCII-art welcome banner with live system info
- Persistent configuration at ~/.khavis/cli_config.json
- Interactive settings menu (/config)
- Enhanced REPL with colored status bar and slash commands
- Agent / model / mode / theme switching at runtime

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any

# ── ANSI helpers ──────────────────────────────────────────────────

class C:
    """Terminal colors (respects NO_COLOR / non-TTY)."""
    _enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    @classmethod
    def _c(cls, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if cls._enabled else text

    @classmethod
    def bold(cls, t: str) -> str: return cls._c("1", t)
    @classmethod
    def dim(cls, t: str) -> str: return cls._c("2", t)
    @classmethod
    def cyan(cls, t: str) -> str: return cls._c("96", t)
    @classmethod
    def green(cls, t: str) -> str: return cls._c("92", t)
    @classmethod
    def yellow(cls, t: str) -> str: return cls._c("93", t)
    @classmethod
    def red(cls, t: str) -> str: return cls._c("91", t)
    @classmethod
    def blue(cls, t: str) -> str: return cls._c("94", t)
    @classmethod
    def magenta(cls, t: str) -> str: return cls._c("95", t)
    @classmethod
    def white(cls, t: str) -> str: return cls._c("97", t)
    @classmethod
    def bg_blue(cls, t: str) -> str: return cls._c("44;97", t)
    @classmethod
    def bg_green(cls, t: str) -> str: return cls._c("42;30", t)
    @classmethod
    def bg_yellow(cls, t: str) -> str: return cls._c("43;30", t)


BANNER = r"""
    _  __ ___  _   _  __    _    _   _  ____  _   _
   | |/ /|_ _|| \ | |/ /   / \  | \ | || ___|| | | |
   | ' /  | | |  \| / /   / _ \ |  \| || |   | |_| |
   | . \  | | | |\  / /   / ___ \| |\  || |___|  _  |
   |_|\_\|___||_| \/_/   /_/   \_\_| \_||_____||_| |_|

   ──────────────────────────────────────────────────────
   K.H.A.V.I.S. · Multi-Agent Collaborative Terminal
   ──────────────────────────────────────────────────────
"""

# ── Configuration ─────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".khavis"
CONFIG_PATH = CONFIG_DIR / "cli_config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "agents": ["khavis"],
    "mode": "auto_loop",
    "theme": "dark",
    "show_banner": True,
    "show_timing": True,
    "max_output_lines": 50,
    "notifications": False,
    "agent_models": {},
    "capability_hint": "",
}

VALID_MODES = ["auto_loop", "relay", "critique", "parallel"]
VALID_THEMES = ["dark", "light", "mono"]
ALL_AGENT_IDS = ["claude", "codex", "antigravity", "minimax", "opencode", "cmdcode", "khavis"]


def load_config() -> dict[str, Any]:
    """Load persistent CLI config, merged with defaults."""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.is_file():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CONFIG})
        except (OSError, ValueError):
            pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Persist CLI config to ~/.khavis/cli_config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ── Welcome banner ────────────────────────────────────────────────

def print_banner(cfg: dict[str, Any], agent_rows: list[dict[str, str]] | None = None) -> None:
    """Print the Claude Code-style welcome banner with live system info."""
    if not cfg.get("show_banner", True):
        return

    ready_count = sum(1 for r in (agent_rows or []) if r.get("status") == "ready")
    total = len(agent_rows or ALL_AGENT_IDS)
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    platform_str = f"{platform.system()} {platform.release()}"
    agents_str = ", ".join(cfg.get("agents", ["khavis"]))
    mode_str = cfg.get("mode", "auto_loop")

    lines = [
        C.cyan(BANNER),
        f"  {C.bold('版本')}  {C.dim('v0.5.0')}   "
        f"{C.bold('Python')}  {C.dim(py_ver)}   "
        f"{C.bold('系統')}  {C.dim(platform_str)}",
        "",
        f"  {C.bold('Agent')}  {C.cyan(agents_str)}   "
        f"{C.bold('模式')}  {C.cyan(mode_str)}   "
        f"{C.bold('可用')}  {C.green(str(ready_count))}{C.dim('/' + str(total))}",
        "",
        f"  {C.dim('── 快捷指令 ──────────────────────────────')}",
        f"  {C.yellow('/run')}      執行任務（目前 agent 與模式）",
        f"  {C.yellow('/config')}   開啟設定選單",
        f"  {C.yellow('/agents')}   查看 Agent 狀態",
        f"  {C.yellow('/mode')}     切換協同模式",
        f"  {C.yellow('/new')}      開始新對話",
        f"  {C.yellow('/quit')}     離開",
        f"  {C.dim('────────────────────────────────────────')}",
        "",
    ]
    print("\n".join(lines))


def print_status_bar(cfg: dict[str, Any], turn: int = 0) -> None:
    """Print a compact one-line status bar before the prompt."""
    agents = ", ".join(cfg.get("agents", ["khavis"]))
    mode = cfg.get("mode", "auto_loop")
    thread_info = f" · 第 {turn} 輪" if turn > 0 else ""
    bar = f" {C.bg_blue(' K ')} {C.cyan(agents)} {C.dim('│')} {C.yellow(mode)}{C.dim(thread_info)} "
    print(bar)


# ── Settings menu ─────────────────────────────────────────────────

def _menu_choice(title: str, options: list[str], current: str) -> str | None:
    """Simple numbered menu; returns chosen value or None to cancel."""
    print(f"\n  {C.bold(title)}")
    for i, opt in enumerate(options, 1):
        marker = C.green(" ●") if opt == current else "  "
        print(f"  {C.dim(str(i) + '.')} {marker} {opt}")
    print(f"  {C.dim('0.')}   取消")
    try:
        raw = input(f"  {C.cyan('選擇 › ')}").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if raw == "0" or not raw:
        return None
    idx = int(raw) - 1 if raw.isdigit() else -1
    if 0 <= idx < len(options):
        return options[idx]
    return None


def _toggle_agents(cfg: dict[str, Any]) -> None:
    """Multi-select agent toggle."""
    selected = set(cfg.get("agents", []))
    ids = [a for a in ALL_AGENT_IDS]
    while True:
        print(f"\n  {C.bold('Agent 選擇')}（輸入編號切換，Enter 確認，0 全選/全消）")
        for i, aid in enumerate(ids, 1):
            marker = C.green("✓") if aid in selected else C.dim("○")
            print(f"  {C.dim(str(i) + '.')} {marker} {C.cyan(aid)}")
        try:
            raw = input(f"  {C.cyan('切換 › ')}").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw:
            break
        if raw == "0":
            selected = set(ids) if not selected else set()
            continue
        for part in raw.replace(",", " ").split():
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(ids):
                    if ids[idx] in selected:
                        selected.discard(ids[idx])
                    else:
                        selected.add(ids[idx])
    cfg["agents"] = [a for a in ids if a in selected] or ["khavis"]


def open_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Interactive settings menu loop; returns updated config."""
    while True:
        print(f"\n  {C.bg_blue(' ⚙ 設定 ')}")
        agents_str = C.cyan(", ".join(cfg.get("agents", [])))
        print(f"  {C.dim('1.')} Agent        {agents_str}")
        print(f"  {C.dim('2.')} 協同模式     {C.yellow(cfg.get('mode', 'auto_loop'))}")
        print(f"  {C.dim('3.')} 歡迎畫面     {C.green('開啟') if cfg.get('show_banner') else C.red('關閉')}")
        print(f"  {C.dim('4.')} 顯示耗時     {C.green('開啟') if cfg.get('show_timing') else C.red('關閉')}")
        print(f"  {C.dim('5.')} 輸出行數上限  {C.bold(str(cfg.get('max_output_lines', 50)))}")
        print(f"  {C.dim('6.')} 儲存並離開")
        print(f"  {C.dim('0.')} 取消（不儲存）")
        try:
            raw = input(f"  {C.cyan('設定 › ')}").strip()
        except (EOFError, KeyboardInterrupt):
            return cfg

        if raw == "0":
            return cfg
        elif raw == "1":
            _toggle_agents(cfg)
        elif raw == "2":
            chosen = _menu_choice("協同模式", VALID_MODES, cfg.get("mode", "auto_loop"))
            if chosen:
                cfg["mode"] = chosen
        elif raw == "3":
            cfg["show_banner"] = not cfg.get("show_banner", True)
        elif raw == "4":
            cfg["show_timing"] = not cfg.get("show_timing", True)
        elif raw == "5":
            try:
                n = int(input(f"  {C.cyan('行數 › ')}").strip())
                if 5 <= n <= 500:
                    cfg["max_output_lines"] = n
            except ValueError:
                pass
        elif raw == "6":
            save_config(cfg)
            print(f"  {C.green('✅ 已儲存至 ' + str(CONFIG_PATH))}\n")
            return cfg
