# Relay Quickstart — 一條可重現的接力證據（E2）

這個小範例存在的唯一目的：用**可重現**的方式證明 KHAVIS 的核心工作流——
**接力（planner → coder → reviewer）→ 產出檔案 → git diff → 人工採用**——
在乾淨環境能跑通，而不是只存在於 README 的描述裡。

它有兩條路徑，**都不會自動採用任何產出**：一定會停在人工閘門，由你檢視
diff 後才決定是否 commit。

## 路徑 A：離線骨架（無 key、無網路）

```bash
python3 examples/relay_quickstart/run_offline.py
# 會印出：planner 步驟、reviewer 註記、git diff，然後停在人工閘門（exit 2）

python3 examples/relay_quickstart/run_offline.py --adopt
# 人工確認後才 commit（exit 0）
```

預期看到：

- `stand_in_agents: true`——三個角色是**確定性的本機 stand-in，不是模型**；
- `diff_present: true`——真的有一個新檔案進入 git diff；
- 沒有 `--adopt` 時 `adopted: false`、exit code 2；加上後才 commit。

這證明的是**工作流可重現**，不證明任何模型或 CLI 可用。

## 路徑 B：真實 K.A.V.I.S. 接力（需 BYOK）

前置條件：

```bash
python -m pip install -e ".[agents]"
# .env 至少一個可用的供應商 API key（見 .env.example）
```

執行：

```bash
python3 examples/relay_quickstart/run_byok.py          # 真實團隊接力 + diff，停在人工閘門（exit 2）
python3 examples/relay_quickstart/run_byok.py --adopt  # 人工確認後採用（exit 0）
```

差異：這次是**真的 LangGraph 7 角色團隊**（planner → coder×2 → debater →
critic → verifier → learner）執行一個小任務，輸出含
`stand_in_agents: false` 與 `team: langgraph-7-role`。
會消耗供應商額度；金鑰或配額有問題時請回到路徑 A。

## 這個範例不證明什麼

- 不證明某個供應商或外部 CLI 一定可用；
- 不證明團隊產出品質（內容對不對，由你在人工閘門判斷）；
- 不是效能基準，也不是「10 分鐘上手保證」——安裝時間請另外計。

對應的宣稱分級見 [`docs/EVIDENCE.md`](../../docs/EVIDENCE.md)。
