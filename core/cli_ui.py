"""Rich terminal UI for the ``khavis`` CLI (Claude Code-style welcome + settings).

Provides:
- Crisp K.H.A.V.I.S. ASCII-art welcome banner with live system, workspace & agent info
- Persistent configuration at ~/.khavis/cli_config.json
- Readline command history & slash-command Tab completion (~/.khavis/cli_history)
- Theme-aware ANSI styling (dark / light / mono, respects NO_COLOR)
- Interactive settings menu (/config) for agents, handoff order, mode, models, and display

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import atexit
import contextlib
import copy
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

# ── ANSI helpers ──────────────────────────────────────────────────

_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bold": "1",
        "dim": "2",
        "cyan": "96",
        "green": "92",
        "yellow": "93",
        "red": "91",
        "blue": "94",
        "magenta": "95",
        "white": "97",
        "bg_blue": "44;97",
        "bg_green": "42;30",
        "bg_yellow": "43;30",
    },
    "light": {
        "bold": "1",
        "dim": "2",
        "cyan": "36",
        "green": "32",
        "yellow": "33",
        "red": "31",
        "blue": "34",
        "magenta": "35",
        "white": "30",
        "bg_blue": "44;97",
        "bg_green": "42;97",
        "bg_yellow": "43;30",
    },
}


class C:
    """Terminal colors (theme-aware, respects NO_COLOR / non-TTY)."""

    _tty_ok: bool = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    _theme: str = "dark"
    _enabled: bool = _tty_ok

    @classmethod
    def set_theme(cls, theme: str) -> None:
        t = (theme or "dark").strip().lower()
        cls._theme = t if t in ("dark", "light", "mono") else "dark"
        cls._tty_ok = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        cls._enabled = cls._tty_ok and cls._theme != "mono"

    @classmethod
    def _c(cls, key: str, text: str) -> str:
        if not cls._enabled:
            return text
        palette = _PALETTES.get(cls._theme, _PALETTES["dark"])
        code = palette.get(key, key)
        return f"\033[{code}m{text}\033[0m"

    @classmethod
    def bold(cls, t: str) -> str:
        return cls._c("bold", t)

    @classmethod
    def dim(cls, t: str) -> str:
        return cls._c("dim", t)

    @classmethod
    def cyan(cls, t: str) -> str:
        return cls._c("cyan", t)

    @classmethod
    def green(cls, t: str) -> str:
        return cls._c("green", t)

    @classmethod
    def yellow(cls, t: str) -> str:
        return cls._c("yellow", t)

    @classmethod
    def red(cls, t: str) -> str:
        return cls._c("red", t)

    @classmethod
    def blue(cls, t: str) -> str:
        return cls._c("blue", t)

    @classmethod
    def magenta(cls, t: str) -> str:
        return cls._c("magenta", t)

    @classmethod
    def white(cls, t: str) -> str:
        return cls._c("white", t)

    @classmethod
    def bg_blue(cls, t: str) -> str:
        return cls._c("bg_blue", t)

    @classmethod
    def bg_green(cls, t: str) -> str:
        return cls._c("bg_green", t)

    @classmethod
    def bg_yellow(cls, t: str) -> str:
        return cls._c("bg_yellow", t)


BANNER = r"""
   _  __ _   _    _   __     __ ___  ____
  | |/ /| | | |  / \  \ \   / /|_ _|/ ___|
  | ' / | |_| | / _ \  \ \ / /  | | \___ \
  | . \ |  _  |/ ___ \  \ V /   | |  ___) |
  |_|\_\|_| |_/_/   \_\  \_/   |___||____/
"""

# ── Configuration ─────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".khavis"
CONFIG_PATH = CONFIG_DIR / "cli_config.json"
HISTORY_PATH = CONFIG_DIR / "cli_history"

VALID_MODES = ["auto_loop", "relay", "preempt", "critique", "parallel"]
MODE_LABELS: dict[str, str] = {
    "auto_loop": "全自動閉環（並發實作 → 交互審查 → 匯整交付）",
    "relay": "順序接力（依排定順序棒棒交接深化）",
    "preempt": "搶佔交接（主棒優先執行，失敗自動交棒下一順位）",
    "critique": "雙向互評（獨立實作 → 交叉挑刺與補強）",
    "parallel": "並發競速（多 Agent 同時執行並擇優共識）",
}
MODE_SHORT: dict[str, str] = {
    "auto_loop": "全自動閉環",
    "relay": "順序接力",
    "preempt": "搶佔交接",
    "critique": "雙向互評",
    "parallel": "並發競速",
}

VALID_THEMES = ["dark", "light", "mono"]
ALL_AGENT_IDS = [
    "claude",
    "codex",
    "antigravity",
    "minimax",
    "opencode",
    "cmdcode",
    "khavis",
    "kavis-tencent",
]

SLASH_COMMANDS = [
    "/config",
    "/settings",
    "/agents",
    "/order",
    "/mode",
    "/model",
    "/history",
    "/resume",
    "/full",
    "/steps",
    "/run",
    "/new",
    "/save",
    "/help",
    "/quit",
    "/exit",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "agents": ["khavis"],
    "mode": "auto_loop",
    "theme": "dark",
    "show_banner": True,
    "show_timing": True,
    "max_output_lines": 80,
    "notifications": False,
    "agent_models": {},
    "capability_hint": "",
}


def load_config() -> dict[str, Any]:
    """Load persistent CLI config, merged with defaults."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.is_file():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for k, v in saved.items():
                    if k in DEFAULT_CONFIG:
                        cfg[k] = v
        except (OSError, ValueError):
            pass
    if not isinstance(cfg.get("agents"), list) or not cfg["agents"]:
        cfg["agents"] = ["khavis"]
    if cfg.get("mode") not in VALID_MODES:
        cfg["mode"] = "auto_loop"
    if not isinstance(cfg.get("agent_models"), dict):
        cfg["agent_models"] = {}
    C.set_theme(str(cfg.get("theme", "dark")))
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Persist CLI config to ~/.khavis/cli_config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ── Readline history & Tab completion ────────────────────────────

_READLINE_READY = False


def setup_readline(extra_agent_ids: list[str] | None = None) -> None:
    """Enable persistent prompt history and Tab completion for slash commands."""
    global _READLINE_READY
    if _READLINE_READY or not sys.stdin.isatty():
        return
    try:
        import readline  # noqa: PLC0415
    except ImportError:
        return

    _READLINE_READY = True
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if HISTORY_PATH.is_file():
        with contextlib.suppress(OSError):
            readline.read_history_file(str(HISTORY_PATH))
    readline.set_history_length(500)

    def _save_hist() -> None:
        with contextlib.suppress(OSError):
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(str(HISTORY_PATH))

    atexit.register(_save_hist)

    agent_pool = list(dict.fromkeys([*ALL_AGENT_IDS, *(extra_agent_ids or [])]))

    def _completer(text: str, state: int) -> str | None:
        buf = readline.get_line_buffer().lstrip()
        candidates: list[str] = []
        if buf.startswith("/mode "):
            prefix = text.strip().lower()
            candidates = [m for m in VALID_MODES if m.startswith(prefix)]
        elif buf.startswith(("/agents ", "/order ")):
            prefix = text.strip().lower()
            candidates = [a for a in agent_pool if a.startswith(prefix)]
        elif buf.startswith("/") or text.startswith("/"):
            candidates = [c for c in SLASH_COMMANDS if c.startswith(text)]
        return candidates[state] if state < len(candidates) else None

    try:
        readline.set_completer_delims(" \t\n,")
        readline.set_completer(_completer)
        doc = getattr(readline, "__doc__", "") or ""
        if "libedit" in doc:
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
    except Exception:  # noqa: BLE001
        pass


# ── Formatting helpers ───────────────────────────────────────────


def format_agent_chain(agents: list[str], mode: str, models: dict[str, str] | None = None) -> str:
    """Render agent chain (`→` for ordered modes, `+` for concurrent modes)."""
    sep = " → " if mode in ("relay", "preempt") else " + "
    m_map = models or {}
    parts: list[str] = []
    for aid in agents:
        m = str(m_map.get(aid) or "").strip()
        parts.append(f"{aid}({m})" if m else aid)
    return sep.join(parts) if parts else "khavis"


# ── Welcome banner ────────────────────────────────────────────────


def print_banner(
    cfg: dict[str, Any],
    agent_rows: list[dict[str, str]] | None = None,
    workspace: Path | None = None,
) -> None:
    """Print the Claude Code-style welcome banner with live system info."""
    if not cfg.get("show_banner", True):
        return

    from .cli import VERSION  # noqa: PLC0415

    ready_count = sum(1 for r in (agent_rows or []) if r.get("status") == "ready")
    total = len(agent_rows or ALL_AGENT_IDS)
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    platform_str = f"{platform.system()} {platform.release()}"
    mode_str = str(cfg.get("mode", "auto_loop"))
    mode_desc = MODE_SHORT.get(mode_str, mode_str)
    agents_str = format_agent_chain(
        cfg.get("agents", ["khavis"]), mode_str, cfg.get("agent_models")
    )
    ws_str = str(workspace or Path.cwd())
    home = str(Path.home())
    if ws_str.startswith(home):
        ws_str = "~" + ws_str[len(home) :]

    lines = [
        C.cyan(BANNER.rstrip("\n")),
        f"  {C.dim('──────────────────────────────────────────────────────────')}",
        f"  {C.bold('K.H.A.V.I.S.')} · {C.dim('多 Agent 協同終端機工作台')}  {C.dim('v' + VERSION)}",
        f"  {C.dim('──────────────────────────────────────────────────────────')}",
        f"  {C.bold('環境')}  {C.dim(f'Python {py_ver} · {platform_str}')}",
        f"  {C.bold('目錄')}  {C.dim(ws_str)}",
        f"  {C.bold('團隊')}  {C.cyan(agents_str)}   "
        f"{C.bold('就緒')}  {C.green(str(ready_count))}{C.dim('/' + str(total))}",
        f"  {C.bold('模式')}  {C.yellow(mode_str)} {C.dim(f'({mode_desc})')}",
        "",
        f"  {C.dim('── 快捷指令（支援 Tab 自動補全）──────────────────────────')}",
        f"  {C.yellow('/config')}     互動設定選單（Agent 順序、模式、模型、主題）",
        f"  {C.yellow('/agents')}     檢視所有內建與自訂 Agent 就緒狀態",
        f"  {C.yellow('/order a,b')}  設定接力／搶佔交接順序（例：/order claude,codex）",
        f"  {C.yellow('/mode <m>')}   切換協同模式（{' / '.join(VALID_MODES)}）",
        f"  {C.yellow('/history')}    檢視歷史對話串（與 Web 工作台雙向同步）",
        f"  {C.yellow('/resume <n>')} 從指定歷史對話串接續對話",
        f"  {C.yellow('/steps')}      查看上一輪各階段執行明細與交叉審查   {C.yellow('/full')} 完整輸出",
        f"  {C.yellow('/new')}        開啟新對話串   {C.yellow('/save')} 儲存設定   {C.yellow('/quit')} 離開",
        f"  {C.dim('──────────────────────────────────────────────────────────')}",
        "",
    ]
    print("\n".join(lines))


def print_status_bar(cfg: dict[str, Any], turn: int = 0, thread_id: str | None = None) -> None:
    """Print a compact one-line status bar before the prompt."""
    mode = str(cfg.get("mode", "auto_loop"))
    agents = format_agent_chain(cfg.get("agents", ["khavis"]), mode, cfg.get("agent_models"))
    mode_tag = f"{mode}·{MODE_SHORT.get(mode, mode)}"
    meta = ""
    if turn > 0:
        tid_part = f" #{thread_id}" if thread_id else ""
        meta = f" · 第 {turn} 輪{tid_part}"
    bar = f" {C.bg_blue(' K ')} {C.cyan(agents)} {C.dim('│')} {C.yellow(mode_tag)}{C.dim(meta)} "
    print(bar)


# ── Settings menu ─────────────────────────────────────────────────


def _menu_choice(
    title: str,
    options: list[str],
    current: str,
    descriptions: dict[str, str] | None = None,
) -> str | None:
    """Numbered menu; returns chosen value or None to cancel."""
    print(f"\n  {C.bold(title)}")
    for i, opt in enumerate(options, 1):
        marker = C.green(" ●") if opt == current else "  "
        desc = f"  {C.dim(descriptions[opt])}" if descriptions and opt in descriptions else ""
        print(f"  {C.dim(str(i) + '.')} {marker} {C.cyan(opt)}{desc}")
    print(f"  {C.dim('0.')}    取消")
    try:
        raw = input(f"  {C.cyan('選擇 › ')}").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if raw == "0" or not raw:
        return None
    if raw in options:
        return raw
    idx = int(raw) - 1 if raw.isdigit() else -1
    if 0 <= idx < len(options):
        return options[idx]
    return None


def _toggle_agents(cfg: dict[str, Any], available_ids: list[str] | None = None) -> None:
    """Ordered multi-select agent selector."""
    ids = list(dict.fromkeys([*(available_ids or ALL_AGENT_IDS), *cfg.get("agents", [])]))
    ordered: list[str] = [a for a in cfg.get("agents", []) if a in ids]
    while True:
        chain_preview = " → ".join(ordered) if ordered else "（未選擇，預設回退 khavis）"
        print(
            f"\n  {C.bold('Agent 選擇與交接順序')}\n"
            f"  {C.dim('提示：輸入單一編號可切換勾選；輸入多個編號（如 2 1）可直接重排交接順序；0 清空/全選；Enter 完成')}"
        )
        print(f"  {C.bold('目前順序：')}{C.cyan(chain_preview)}")
        for i, aid in enumerate(ids, 1):
            if aid in ordered:
                pos = ordered.index(aid) + 1
                marker = C.green(f"[{pos}]")
            else:
                marker = C.dim(" ○ ")
            print(f"  {C.dim(str(i).rjust(2) + '.')} {marker} {C.cyan(aid)}")
        try:
            raw = input(f"  {C.cyan('編號 › ')}").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw:
            break
        if raw == "0":
            ordered = [] if ordered else list(ids)
            continue
        tokens = [p for p in raw.replace(",", " ").split() if p]
        nums = [int(p) - 1 for p in tokens if p.isdigit() and 0 <= int(p) - 1 < len(ids)]
        if len(nums) >= 2:
            # Explicit sequence sets exact handoff order
            ordered = list(dict.fromkeys(ids[n] for n in nums))
        elif len(nums) == 1:
            aid = ids[nums[0]]
            if aid in ordered:
                ordered.remove(aid)
            else:
                ordered.append(aid)
    cfg["agents"] = ordered or ["khavis"]


def _configure_models(cfg: dict[str, Any], available_ids: list[str] | None = None) -> None:
    """Configure per-agent model overrides."""
    ids = list(dict.fromkeys([*(available_ids or ALL_AGENT_IDS), *cfg.get("agents", [])]))
    models: dict[str, str] = dict(cfg.get("agent_models") or {})
    while True:
        print(
            f"\n  {C.bold('各 Agent 指定模型')} {C.dim('（留空代表使用系統／環境變數預設模型）')}"
        )
        for i, aid in enumerate(ids, 1):
            cur = models.get(aid) or ""
            label = C.yellow(cur) if cur else C.dim("（預設）")
            print(f"  {C.dim(str(i).rjust(2) + '.')} {C.cyan(aid.ljust(16))} {label}")
        print(f"  {C.dim(' 0.')} 返回設定選單")
        try:
            raw = input(f"  {C.cyan('選擇 Agent 編號 › ')}").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw or raw == "0":
            break
        if raw.isdigit() and 1 <= int(raw) <= len(ids):
            aid = ids[int(raw) - 1]
            cur = models.get(aid, "")
            cur_label = cur or "預設"
            try:
                val = input(
                    f"  {C.cyan(f'{aid} 模型名稱')} {C.dim(f'[目前: {cur_label}，輸入 - 清除]')} › "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if val == "-":
                models.pop(aid, None)
            elif val:
                models[aid] = val
    cfg["agent_models"] = models


def open_settings(cfg: dict[str, Any], available_ids: list[str] | None = None) -> dict[str, Any]:
    """Interactive settings menu loop; returns updated config (or original on cancel)."""
    draft = copy.deepcopy(cfg)
    orig_theme = str(cfg.get("theme", "dark"))

    while True:
        mode = str(draft.get("mode", "auto_loop"))
        agents_str = C.cyan(
            format_agent_chain(draft.get("agents", []), mode, draft.get("agent_models"))
        )
        models_count = len([v for v in (draft.get("agent_models") or {}).values() if v])
        max_l = int(draft.get("max_output_lines", 80))
        max_label = "不截斷（全部顯示）" if max_l <= 0 else f"{max_l} 行"

        print(f"\n  {C.bg_blue(' ⚙ CLI 偏好設定 ')}")
        print(f"  {C.dim('1.')} Agent 與順序   {agents_str}")
        print(
            f"  {C.dim('2.')} 協同模式       {C.yellow(mode)} {C.dim('(' + MODE_SHORT.get(mode, '') + ')')}"
        )
        print(
            f"  {C.dim('3.')} 指定模型       "
            f"{C.yellow(f'已自訂 {models_count} 個') if models_count else C.dim('全部使用預設')}"
        )
        print(f"  {C.dim('4.')} 配色主題       {C.cyan(str(draft.get('theme', 'dark')))}")
        print(
            f"  {C.dim('5.')} 歡迎畫面       "
            f"{C.green('開啟') if draft.get('show_banner') else C.dim('關閉')}"
        )
        print(
            f"  {C.dim('6.')} 顯示耗時       "
            f"{C.green('開啟') if draft.get('show_timing') else C.dim('關閉')}"
        )
        print(f"  {C.dim('7.')} 輸出行數上限   {C.bold(max_label)}")
        print(f"  {C.dim('8.')} {C.green('儲存並套用')}")
        print(f"  {C.dim('0.')} 取消（還原變更）")
        try:
            raw = input(f"  {C.cyan('設定 › ')}").strip()
        except (EOFError, KeyboardInterrupt):
            C.set_theme(orig_theme)
            return cfg

        if raw == "0":
            C.set_theme(orig_theme)
            print(f"  {C.dim('已取消變更。')}\n")
            return cfg
        if raw == "1":
            _toggle_agents(draft, available_ids)
        elif raw == "2":
            chosen = _menu_choice("協同模式", VALID_MODES, mode, MODE_LABELS)
            if chosen:
                draft["mode"] = chosen
        elif raw == "3":
            _configure_models(draft, available_ids)
        elif raw == "4":
            theme_desc = {
                "dark": "深色終端機高對比配色（預設）",
                "light": "淺色終端機深色字體配色",
                "mono": "純文字無色彩模式",
            }
            chosen = _menu_choice(
                "配色主題", VALID_THEMES, str(draft.get("theme", "dark")), theme_desc
            )
            if chosen:
                draft["theme"] = chosen
                C.set_theme(chosen)
        elif raw == "5":
            draft["show_banner"] = not draft.get("show_banner", True)
        elif raw == "6":
            draft["show_timing"] = not draft.get("show_timing", True)
        elif raw == "7":
            try:
                val = input(f"  {C.cyan('行數上限（輸入 0 代表不截斷，5~1000）› ')}").strip()
                n = int(val)
                if n == 0 or 5 <= n <= 1000:
                    draft["max_output_lines"] = n
            except ValueError:
                pass
        elif raw == "8":
            save_config(draft)
            C.set_theme(str(draft.get("theme", "dark")))
            print(f"  {C.green('✅ 已儲存並套用至 ' + str(CONFIG_PATH))}\n")
            return draft
