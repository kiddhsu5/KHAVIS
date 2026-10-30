# Changelog

本專案的版本與功能狀態以此檔案、實作與測試為準。語意化版本：`MAJOR.MINOR.PATCH`。

## [0.5.0] — 2026-10-30

第一個對外公開的完整工作台版本（public release）。

### 工作台（Web Workbench）

- 對話串為中心的任務流：歷史談話可依時間線、專案、執行節點、協同模式、狀態分組與搜尋。
- 每輪任務的產出文檔以內聯清單呈現（副檔名徽章、下載、預覽）；該輪沒有產出就不顯示區塊。
- 「執行明細」全頁：任務、Agent 陣容、產出文檔、共識結論與 Agent 交叉審查比較。
- 資料庫按對話串主題歸檔產出文檔，找不到來源者另列未歸檔群組；提供格式篩選、搜尋、預覽與下載。
- Agent、資料庫、額度與部署設定以獨立全頁呈現，Esc 返回對話串。
- Morandi 配色，深淺色主題可切換。

### Agent 與模型管理

- 供應商模型列舉改為「從供應商即時清單抓取 → 使用者勾選」：勾選結果是權威清單，刪除不會被供應商全量清單復活。
- Agent 頁提供全頁勾選器：依供應商分組、搜尋、手動新增、儲存；取消勾選當前模型時自動切換到第一個勾選項。
- 供應商 thinking／reasoning 模式依各供應商回傳的 capability metadata 呈現，不臆測未回傳的模式。

### K.A.V.I.S. 內建 agent

- 項目內建的協同 agent：基於 openclaw × hermes 執行環境，採用 LangGraph 7 角色團隊協同（Planner / Coder ×2 / Debater / Critic / Verifier / Learner）。
- 同一 agent 可視設定使用本地 LangGraph 團隊，或透過 `deploy/kavis_adapter.py`（OpenAI 相容、僅綁 localhost）派工到遠端 hermes 節點。

### 路由與整合

- Provider registry、capability-based selection、pool 健康與熔斷、預算與稽核。
- `khavis chat`（免 daemon）、`khavis doctor`、`khavis serve`（OpenAI 相容 HTTP API）。
- 外部 CLI agent 整合（需自行安裝與登入）：Claude Code、Codex、Antigravity、MiniMax Code、OpenCode，以及使用者自訂 CLI／HTTP agent。
- 選用 Telegram bot、Docker、VM 部署。

### 部署

- `deploy/install.sh`：macOS / Linux 一鍵安裝（venv、依賴、`.env` 初始化、systemd 服務、健康檢查）。
- `deploy/install.ps1`：Windows 一鍵安裝（venv、依賴、`.env` 初始化、開機排程、健康檢查）。
- 舊有非 git 部署自動備份為 `<home>-preinstall-<timestamp>`，不覆蓋既有 `.env`。

### 文件

- 英文與繁體中文 README、安裝與設定指南、架構與健康說明、Plugin 開發指南、部署指南、貢獻與安全政策。

### 測試

- 離線 mock 故障流程演練（`examples/zero_quota_demo.py`，不需 key、網路或 Ollama）。
- 單元測試涵蓋路由、供應商能力探索、模型勾選持久化、附件防護、工作台 API 與前端契約。

[0.5.0]: https://github.com/kiddhsu5/KHAVIS/releases/tag/v0.5.0
