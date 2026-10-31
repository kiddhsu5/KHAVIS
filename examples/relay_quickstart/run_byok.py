#!/usr/bin/env python3
"""K.H.A.V.I.S. relay quickstart — 真實 K.A.V.I.S. 接力（需 BYOK）。

執行真正的 LangGraph 團隊（planner → coder×2 → debater → critic → verifier
→ learner）跑一個小任務，把產出寫入暫存 git repo，印出 diff，
最後由「你」決定是否採用（--adopt 才 commit）。

前置條件：
  1. 在 repo 根目錄安裝 agents extra：
       python -m pip install -e ".[agents]"
  2. .env 至少有一個可用的供應商 API key（見 .env.example）。
     沒有 key 時請改用 run_offline.py（離線骨架）。

用法（在 repo 根目錄）：
    python3 examples/relay_quickstart/run_byok.py            # 執行 + diff，停在人工閘門（exit 2）
    python3 examples/relay_quickstart/run_byok.py --adopt    # 人工確認後採用（exit 0）
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

TASK = (
    "用 Python 寫一個 summarize(numbers) 函式，回傳 count/mean/min/max；"
    "空輸入要 raise ValueError。只需要一個小函式與必要的邊界處理。"
)


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def main() -> int:
    adopt = "--adopt" in sys.argv
    try:
        from agents.graph import run_team  # noqa: PLC0415
    except ImportError:
        print("找不到 agents extra。請先執行：python -m pip install -e \".[agents]\"", file=sys.stderr)
        return 1

    from core.env import load_env  # noqa: PLC0415

    load_env()  # 載入 repo 根目錄 .env（若存在）

    print(f"執行 K.A.V.I.S. 團隊接力：{TASK}\n（真實供應商請求，可能耗費額度）", flush=True)
    try:
        state = run_team(TASK, session_id="relay-quickstart")
    except Exception as exc:  # noqa: BLE001
        print(f"團隊執行失敗：{exc}\n若是金鑰或配額問題，請改用 run_offline.py。", file=sys.stderr)
        return 1

    final = str(state.get("final") or state.get("solution") or "")
    if not final.strip():
        print(f"團隊沒有產出可用的 final（state keys: {list(state)[:12]}）。", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="khavis-relay-byok-") as tmp:
        repo = Path(tmp) / "relay-workspace"
        repo.mkdir()
        run_git(repo, "init", "-q")
        run_git(repo, "config", "user.email", "byok@example.invalid")
        run_git(repo, "config", "user.name", "BYOK Relay")
        (repo / "baseline.md").write_text("# relay baseline\n", encoding="utf-8")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-qm", "baseline")

        deliverable = repo / "summarize.py"
        deliverable.write_text(final, encoding="utf-8")
        run_git(repo, "add", "-N", deliverable.name)  # untracked 也要進 diff
        diff = run_git(repo, "diff", "--stat") + "\n" + run_git(repo, "diff")

        summary = {
            "stand_in_agents": False,
            "team": "langgraph-7-role",
            "file_produced": deliverable.name,
            "diff_present": bool(diff.strip()),
            "final_chars": len(final),
        }
        print("\n== 產出與 diff ==")
        print(diff)

        if not adopt:
            summary["adopted"] = False
            summary["gate"] = "human_review_required"
            print("== 人工閘門 ==\n尚未採用。請檢視上方 diff 後，以 --adopt 重跑才會 commit。")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 2

        run_git(repo, "add", "summarize.py")
        run_git(repo, "commit", "-qm", "adopt: summarize.py after human review")
        summary["adopted"] = True
        summary["commit"] = run_git(repo, "rev-parse", "--short", "HEAD").strip()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
