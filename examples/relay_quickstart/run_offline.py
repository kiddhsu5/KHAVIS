#!/usr/bin/env python3
"""K.H.A.V.I.S. relay quickstart — 離線可重現的接力骨架（E2，無 key、無網路）。

這支腳本**不是 LLM 演示**：三個角色是確定性的本機 stand-in（planner →
coder → reviewer），用來驗證「接力 → 產出檔案 → git diff → 人工採用」這條
工作流本身可重現。它不證明任何模型或 CLI 可用。

用法：
    python3 examples/relay_quickstart/run_offline.py            # 產出 + diff，停在人工閘門（exit 2）
    python3 examples/relay_quickstart/run_offline.py --adopt    # 人工確認後採用（exit 0）
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# 離線 stand-in：輸出是固定模板，明確標示非模型產物
CODER_OUTPUT = '''"""Offline stand-in deliverable (NOT model-generated)."""


def summarize(numbers: list[float]) -> dict[str, float]:
    """Return count/mean/min/max for a non-empty numeric list."""
    if not numbers:
        raise ValueError("summarize() expects a non-empty list")
    return {
        "count": len(numbers),
        "mean": sum(numbers) / len(numbers),
        "min": min(numbers),
        "max": max(numbers),
    }
'''

REVIEWER_NOTES = (
    "reviewer(offline): 檢查空輸入會 raise ValueError；count/mean/min/max 欄位齊全；"
    "無外部副作用。建議人工再核對一次邊界行為。"
)


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def main() -> int:
    adopt = "--adopt" in sys.argv
    with tempfile.TemporaryDirectory(prefix="khavis-relay-offline-") as tmp:
        repo = Path(tmp) / "relay-workspace"
        repo.mkdir()
        run_git(repo, "init", "-q")
        run_git(repo, "config", "user.email", "offline@example.invalid")
        run_git(repo, "config", "user.name", "Offline Stand-in")
        (repo / "baseline.md").write_text("# relay baseline\n", encoding="utf-8")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-qm", "baseline")

        # ── 接力：planner → coder → reviewer ──────────────────────
        plan = ["定義 summarize() 的輸入輸出契約", "處理空輸入", "產出單一 .py 檔"]
        deliverable = repo / "summarize.py"
        deliverable.write_text(CODER_OUTPUT, encoding="utf-8")
        run_git(repo, "add", "-N", deliverable.name)  # untracked 也要進 diff

        diff = run_git(repo, "diff", "--stat") + "\n" + run_git(repo, "diff")
        print("== planner steps ==")
        for i, step in enumerate(plan, 1):
            print(f"  {i}. {step}")
        print("\n== reviewer notes ==")
        print(f"  {REVIEWER_NOTES}")
        print("\n== git diff ==")
        print(diff)

        summary = {
            "external_network": False,
            "api_keys_required": False,
            "stand_in_agents": True,
            "steps": plan,
            "file_produced": deliverable.name,
            "diff_present": bool(diff.strip()),
        }

        if not adopt:
            summary["adopted"] = False
            summary["gate"] = "human_review_required"
            print("== 人工閘門 ==")
            print("尚未採用。請檢視上方 diff 後，以 --adopt 重跑才會 commit。")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 2

        run_git(repo, "add", "summarize.py")
        run_git(repo, "commit", "-qm", "adopt: summarize.py after human review")
        summary["adopted"] = True
        summary["commit"] = run_git(repo, "rev-parse", "--short", "HEAD").strip()
        print("== 已採用 ==")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
