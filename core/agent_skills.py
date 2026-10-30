"""Executable Agent Skills Library for K.H.A.V.I.S. Autonomous Agents.

Empowers all KHAVIS agents (antigravity, codex, minimax, opencode, claude, khavis)
with concrete, reliable action skills so they actively execute changes, write files,
configure git remotes, scaffold missing project files, and run tests instead of
merely giving passive advice.

Supported Tools / Skills:
1. ``run_shell``        - Run shell commands in the workspace
2. ``read_file``        - Read a file in the workspace
3. ``write_file``       - Create or overwrite a file in the workspace
4. ``edit_file``        - Replace a target string in an existing file
5. ``list_dir``         - List directory structure and files in the workspace
6. ``search_code``      - Search for regex/text patterns across workspace files
7. ``git_ops``          - Inspect/configure git status, remote, branch, diff, commit
8. ``run_tests``        - Run pytest / unittest / syntax verification in the workspace
9. ``scaffold_project`` - Auto-scaffold missing README, LICENSE, tests, scripts, examples, CI, and git remote

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

# Block destructive host-wiping commands while allowing normal dev/ops commands
_DANGEROUS_CMD_RE = re.compile(
    r"(?:\brm\s+-[rf]{1,2}\s+/(?:\s|$)|:\(\)\s*\{|\bmkfs\b|\bdd\s+if=.*of=/dev/|\bshutdown\b|\breboot\b)",
    re.IGNORECASE,
)

SKILL_DEFINITIONS: list[dict[str, str]] = [
    {
        "id": "run_shell",
        "name": "Shell Command Executor",
        "usage": '{"tool": "run_shell", "command": "pytest -q"}',
        "description": "在專案工作區或雲端主機上執行真實 Shell 指令並回傳 stdout/stderr。",
    },
    {
        "id": "write_file",
        "name": "Workspace File Writer",
        "usage": '{"tool": "write_file", "path": "README.md", "content": "# Project\\n"}',
        "description": "在專案工作區內建立或覆寫檔案（自動建立父目錄）。",
    },
    {
        "id": "edit_file",
        "name": "Targeted File Editor",
        "usage": '{"tool": "edit_file", "path": "app.py", "target": "old", "replacement": "new"}',
        "description": "精準替換現有檔案中的指定片段，無需重寫整份檔案。",
    },
    {
        "id": "read_file",
        "name": "Workspace File Reader",
        "usage": '{"tool": "read_file", "path": "README.md"}',
        "description": "讀取專案工作區內的檔案內容。",
    },
    {
        "id": "list_dir",
        "name": "Directory Tree Inspector",
        "usage": '{"tool": "list_dir", "path": "."}',
        "description": "列出專案目錄結構與檔案清單，快速掌握專案現況。",
    },
    {
        "id": "search_code",
        "name": "Codebase Pattern Search",
        "usage": '{"tool": "search_code", "pattern": "def main"}',
        "description": "在工作區程式碼中搜尋關鍵字或正規表示式。",
    },
    {
        "id": "git_ops",
        "name": "Git Repository Manager",
        "usage": '{"tool": "git_ops", "action": "ensure_remote", "remote_url": "https://github.com/kiddhsu5/KHAVIS.git"}',
        "description": "執行 Git 檢查與設定（status, remote, ensure_remote, diff, log）。",
    },
    {
        "id": "run_tests",
        "name": "Automated Test Runner",
        "usage": '{"tool": "run_tests", "target": "tests"}',
        "description": "自動執行 pytest / unittest 測試套件並回傳詳細測試結果。",
    },
    {
        "id": "scaffold_project",
        "name": "Project Completeness Scaffolder",
        "usage": '{"tool": "scaffold_project", "remote_url": "https://github.com/kiddhsu5/KHAVIS.git"}',
        "description": "一鍵補齊專案缺少的 Git 遠端設定、完整開發者 README、LICENSE、CONTRIBUTING、測試、執行腳本、範例與 GitHub Actions CI。",
    },
]


def _resolve_safe_path(work_dir: Path, rel_path: str) -> Path | None:
    target = (
        (work_dir / rel_path).resolve() if not os.path.isabs(rel_path) else Path(rel_path).resolve()
    )
    if not str(target).startswith(str(work_dir)) and not str(target).startswith("/tmp"):
        return None
    return target


def execute_skill(call_obj: dict[str, Any], cwd: str | None = None) -> str:
    """Execute an agent skill call inside `cwd` and return structured execution output."""
    tool = str(call_obj.get("tool") or "").strip().lower()
    work_dir = Path(cwd).resolve() if cwd and os.path.isdir(cwd) else Path.cwd()

    if tool == "run_shell":
        cmd = str(call_obj.get("command") or "").strip()
        if not cmd:
            return "[Error] Empty command"
        if _DANGEROUS_CMD_RE.search(cmd):
            return f"[Blocked] Destructive command rejected by safety guard: {cmd}"
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            combined = "\n".join(part for part in [out, err] if part).strip()
            if len(combined) > 3500:
                combined = combined[:3500] + "\n...[truncated]"
            return f"$ {cmd}\n[exit_code={proc.returncode}]\n{combined or '(no output)'}"
        except subprocess.TimeoutExpired:
            return f"$ {cmd}\n[Error] Command timed out after 25s"
        except Exception as exc:  # noqa: BLE001
            return f"$ {cmd}\n[Error] {exc}"

    if tool == "write_file":
        rel_path = str(call_obj.get("path") or "").strip()
        content = str(call_obj.get("content") or "")
        if not rel_path:
            return "[Error] Missing file path"
        target = _resolve_safe_path(work_dir, rel_path)
        if target is None:
            return f"[Blocked] Write path must be inside workspace ({work_dir}) or /tmp: {rel_path}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"[OK] Wrote {len(content)} chars to {target}"
        except Exception as exc:  # noqa: BLE001
            return f"[Error] Failed to write {target}: {exc}"

    if tool == "edit_file":
        rel_path = str(call_obj.get("path") or "").strip()
        target_str = str(call_obj.get("target") or "")
        replacement_str = str(call_obj.get("replacement") or "")
        if not rel_path or not target_str:
            return "[Error] edit_file requires 'path' and 'target'"
        target = _resolve_safe_path(work_dir, rel_path)
        if target is None:
            return f"[Blocked] Edit path must be inside workspace ({work_dir}) or /tmp: {rel_path}"
        if not target.is_file():
            return f"[Error] File not found: {target}"
        try:
            original = target.read_text(encoding="utf-8", errors="replace")
            if target_str not in original:
                return f"[Error] Target snippet not found in {target.name}"
            updated = original.replace(target_str, replacement_str)
            target.write_text(updated, encoding="utf-8")
            return f"[OK] Edited {target} (replaced target snippet)"
        except Exception as exc:  # noqa: BLE001
            return f"[Error] Failed to edit {target}: {exc}"

    if tool == "read_file":
        rel_path = str(call_obj.get("path") or "").strip()
        if not rel_path:
            return "[Error] Missing file path"
        target = (
            (work_dir / rel_path).resolve()
            if not os.path.isabs(rel_path)
            else Path(rel_path).resolve()
        )
        if target.name.startswith(".env") or target.suffix in {".pem", ".key"}:
            return "[Blocked] Cannot read secret key/env files"
        try:
            if not target.is_file():
                return f"[Error] File not found: {target}"
            content = target.read_text(encoding="utf-8", errors="replace")[:3500]
            return f"[File: {target}]\n{content}"
        except Exception as exc:  # noqa: BLE001
            return f"[Error] Failed to read {target}: {exc}"

    if tool == "list_dir":
        rel_path = str(call_obj.get("path") or ".").strip()
        target = _resolve_safe_path(work_dir, rel_path)
        if target is None or not target.exists():
            return f"[Error] Directory not found: {rel_path}"
        entries: list[str] = []
        for root, dirs, files in os.walk(target):
            dirs[:] = [
                d
                for d in sorted(dirs)
                if d not in {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv"}
            ]
            rel_root = Path(root).relative_to(work_dir)
            prefix = "" if str(rel_root) == "." else f"{rel_root}/"
            for f in sorted(files):
                entries.append(f"{prefix}{f}")
            if len(entries) >= 120:
                entries.append("...[truncated]")
                break
        return f"[Directory: {target}]\n" + ("\n".join(entries) if entries else "(empty directory)")

    if tool == "search_code":
        pattern = str(call_obj.get("pattern") or "").strip()
        if not pattern:
            return "[Error] Missing search pattern"
        matches: list[str] = []
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            rx = re.compile(re.escape(pattern), re.IGNORECASE)
        for root, dirs, files in os.walk(work_dir):
            dirs[:] = [
                d
                for d in sorted(dirs)
                if d not in {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv"}
            ]
            for fname in sorted(files):
                fpath = Path(root) / fname
                if fpath.suffix in {".pyc", ".png", ".jpg", ".sqlite", ".db"}:
                    continue
                try:
                    for lineno, line in enumerate(
                        fpath.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                    ):
                        if rx.search(line):
                            rel = fpath.relative_to(work_dir)
                            matches.append(f"{rel}:{lineno}: {line.strip()[:140]}")
                            if len(matches) >= 50:
                                return (
                                    "[Search Results]\n" + "\n".join(matches) + "\n...[truncated]"
                                )
                except Exception:  # noqa: BLE001
                    continue
        return "[Search Results]\n" + (
            "\n".join(matches) if matches else f"No matches for {pattern!r}"
        )

    if tool == "git_ops":
        action = str(call_obj.get("action") or "status").strip().lower()
        remote_url = str(
            call_obj.get("remote_url") or "https://github.com/kiddhsu5/KHAVIS.git"
        ).strip()
        if action == "ensure_remote":
            chk = subprocess.run(
                ["git", "remote", "-v"],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            if "origin" not in (chk.stdout or ""):
                subprocess.run(
                    ["git", "remote", "add", "origin", remote_url],
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    check=False,
                )
            else:
                subprocess.run(
                    ["git", "remote", "set-url", "origin", remote_url],
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    check=False,
                )
            chk2 = subprocess.run(
                ["git", "remote", "-v"],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            return f"[OK] Configured git remote origin:\n{(chk2.stdout or '').strip()}"
        if action == "remote":
            res = subprocess.run(
                ["git", "remote", "-v"],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            return f"[git remote -v]\n{(res.stdout or '').strip() or '(no remote configured)'}"
        if action == "diff":
            res = subprocess.run(
                ["git", "status", "--short"],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            return f"[git status --short]\n{(res.stdout or '').strip() or '(clean)'}"
        res = subprocess.run(
            ["git", "status", "-sb"], cwd=str(work_dir), capture_output=True, text=True, check=False
        )
        rem = subprocess.run(
            ["git", "remote", "-v"], cwd=str(work_dir), capture_output=True, text=True, check=False
        )
        return f"[git status]\n{(res.stdout or '').strip()}\n[git remote]\n{(rem.stdout or '').strip() or '(none)'}"

    if tool == "run_tests":
        target_arg = str(call_obj.get("target") or "").strip()
        cmd = f"python3 -m pytest -q {target_arg}".strip()
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        out = "\n".join(p for p in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if p)
        if proc.returncode != 0 and "No module named pytest" in out:
            proc = subprocess.run(
                "python3 -m unittest discover -v",
                shell=True,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
            out = "\n".join(
                p for p in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if p
            )
        return f"[Test Runner: exit_code={proc.returncode}]\n{out[:3000]}"

    if tool == "scaffold_project":
        from core.workspace_manager import ensure_project_completeness

        remote_url = str(
            call_obj.get("remote_url") or "https://github.com/kiddhsu5/KHAVIS.git"
        ).strip()
        created = ensure_project_completeness(work_dir, remote_url=remote_url)
        return f"[OK] Project completeness verified in {work_dir}. Created/Updated: {', '.join(created) if created else 'all files already up-to-date'}"

    return f"[Error] Unknown tool/skill: {tool!r}"
