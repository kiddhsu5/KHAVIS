# KHAVIS

> 一個工作區，讓 AI 團隊一起交付。

KHAVIS 整合多個 CLI Agent 與可自行部署的 LLM 路由。你可以共用專案接力，也能以 Git Worktree 隔離任務，再檢視變更與檢查結果，決定採用哪些成果。供應商、額度與工具是否可用，仍取決於實際部署。

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org)
[![Status](https://img.shields.io/badge/status-v0.5.0-emerald.svg)](#roadmap)
[![Project site](https://img.shields.io/badge/Project_Site-khavis.khorus.tech-0EA5E9)](https://khavis.khorus.tech)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[English README](README.md) · 專案網站：[khavis.khorus.tech](https://khavis.khorus.tech) · 選用工作台路徑：[khavis.khorus.tech/workbench](https://khavis.khorus.tech/workbench)

## 工作台：從任務走到可檢查的交付

工作台中央保留任務對話。Agent、服務商與模型設定放在獨立的 `/agents` 頁；文件庫位於 `/database`。模型 ID 與可選 thinking 模式優先使用已連線供應商回傳的資料；沒有能力 metadata 時，不把猜測的模式寫成已支援。

文件庫會按主題歸檔，保留已記錄的來源任務，並提供格式篩選、預覽與下載。分類採本機規則與受限內容樣本，不搬動文件、不呼叫付費模型，也不是完整全文或向量知識庫。格式與邊界見[文件庫與設定說明](docs/CONFIGURATION.md)。

共用工作區適合接力；Git Worktree 適合隔離平行變更。採用成果前，請查看 Diff 與檢查結果。Agent 回覆「完成」本身不等於測試通過或可以合併。KHAVIS 是工作區平台；`K.A.V.I.S.` 是選用的內建 Agent 層。

**從這裡開始：**[快速開始](#快速開始)。若只想先檢查控制流程，請使用下方明確標示的離線 mock 測試；它不會驗證真實供應商或 CLI。

## 本專案中的名稱

- **KHAVIS** 是平台與 repository：包含 provider registry、capability router、設定檔、CLI、選用的 OpenAI 相容 HTTP server、稽核與預算元件，以及 provider plugin。
- **K.A.V.I.S.** 是選用的多代理與部署層所使用的內建 Agent 名稱。依照安裝的 extras 與部署設定，它可以執行本地 LangGraph 團隊，或透過已設定的 `deploy/kavis_adapter.py` 橋接器派送請求。它不是 provider，不會自行保證某個遠端服務或固定模型。
- **外部 CLI 工具**（例如 Claude Code、Codex、Antigravity、MiniMax Code、OpenCode、Aider，或其他本機命令）是整合對象，不是額外的 KHAVIS 模型。它們必須另外安裝、登入並設定；能用哪些模型與命令，取決於該工具與供應商當下的狀態。

目前可用的 pool 由設定檔決定。不要因為 repository 裡存在某個 plugin，就假定相應的 provider、pool、模型別名、API 額度或外部 CLI 一定可用。送出真實請求前，請檢查 `config/pools.yaml`、環境變數與 `khavis doctor` 的結果。

## KHAVIS 提供什麼

KHAVIS 以 plugin 介面隔離不同 provider 的差異，主要包含：

- 使用 `config/capabilities.yaml` 中的標籤與權重進行 capability-based selection；
- 從 `providers/` 自動發現 provider plugin；
- 以 YAML 設定 pool、預算、健康狀態與 Agent；
- 使用 `khavis chat` 在不啟動 server 的情況下直接試用 router；
- 透過 `khavis serve` 提供選用的 OpenAI 相容 HTTP API；
- 逐次請求的預算與健康追蹤，以及 HTTP 路徑的稽核記錄；
- 選用的多代理 orchestration、Telegram 整合與部署橋接器。

Routing 與 failover 是執行期間的最佳努力行為。provider 仍可能無法連線、設定錯誤、遭限流、額度用盡，或沒有請求的模型。KHAVIS 會回報這些失敗，但不承諾每個請求都能不中斷完成。

## 一鍵部署（Windows / macOS / Linux）

安裝器會完成：環境檢查（Python ≥ 3.11）→ clone → venv + 依賴 → `.env` 初始化 → 常駐服務（可選）→ 健康檢查。

```bash
# macOS / Linux（bash）
bash deploy/install.sh

# 含 K.A.V.I.S. 遠端派工橋接器（需要已安裝 hermes 的 Unix 環境）
bash deploy/install.sh --with-adapter

# Windows（PowerShell，於 repo 根目錄）
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\install.ps1
```

不需要常駐服務時加 `--no-service`（macOS 預設也會略過 systemd）。之後升級：`git -C <安裝目錄> pull` 並重啟服務。完整部署說明見 [`deploy/README.md`](deploy/README.md)。

## 快速開始

以下流程從目前 checkout 安裝平台，並建立起始設定檔：

```bash
git clone https://github.com/kiddhsu5/KHAVIS.git
cd KHAVIS
python3.11 -m venv .venv
source .venv/bin/activate              # Windows: .venv\\Scripts\\activate
python -m pip install --upgrade pip
python -m pip install -e .

# 寫入 config/pools.yaml、config/capabilities.yaml、config/agents.yaml.example。
# 已存在的檔案會保留，不會覆寫。
khavis init

# 檢查發現到的 pool 與各 provider 的健康資訊。
khavis doctor
```

在新的 BYOK 環境中，`khavis doctor` 可能會列出尚未設定 key 或 endpoint 不可用的 pool，這是正常的。只為你實際要使用的 provider 填寫 `.env` 與 `config/pools.yaml`。

### 選用安裝 extras

只安裝需要的執行環境：

```bash
python -m pip install -e ".[server]"   # khavis serve / OpenAI 相容 HTTP API
python -m pip install -e ".[agents]"   # 選用的 LangGraph 多代理 runtime
python -m pip install -e ".[bot]"      # 選用的 Telegram bot 元件
python -m pip install -e ".[dev]"      # 測試、lint、型別檢查與打包工具
```

## 兩種不同的測試方式

### 離線 mock smoke test：不需要 key、網路或 Ollama 服務

Repository 內附一個可重現的故障流程測試：

```bash
python3 examples/zero_quota_demo.py --json
```

這個腳本使用記憶體中的 cloud、webhook 與 Ollama 風格 stand-in，驗證本地 routing、額度耗盡處理、告警捕捉與本地 fallback 的控制流程。它**不會**連線到 provider、不會測試真實 Ollama、不會驗證 API key，也不能證明任何特定模型可用。JSON 摘要會包含 `external_network: false`、`api_keys_required: false` 與 `ollama_service_required: false`。

請把它當作離線 smoke test，而不是服務可用性或請求必定完成的承諾。

### 可重現接力證據（E2）

`examples/relay_quickstart/` 在乾淨環境證明核心工作流：接力 → 產出檔案 → git diff → 人工採用。離線路徑不需 key 或網路；BYOK 路徑執行真正的 LangGraph 團隊，且一律停在人工檢視閘門。

### 真實 provider 請求

真實請求需要 provider endpoint 與憑證（真正的本地 Ollama endpoint 除外）：

```bash
# 先編輯 .env 與 config/pools.yaml。
khavis doctor                 # 依 provider 執行健康 / 設定檢查
khavis doctor --deep          # 對每個 ready pool 發出一次真實最小請求
khavis chat "用 Python 寫一個 quicksort。"
```

`doctor --deep` 可能會消耗 provider 額度；只有在你確定要發出真實請求時才執行。`doctor` 與 `doctor --deep` 的意義不同：key、endpoint 或模型名稱已填入，不代表 provider 一定會接受請求。

## 真實 Ollama 設定

Ollama provider 是真實的 HTTP 整合，與 `zero_quota_demo.py` 使用的離線 mock 完全不同：

1. 在提供模型的機器上安裝並啟動 Ollama。
2. 拉取你確定要使用的模型，例如 `ollama pull <model-id>`。
3. 在對應的 `config/pools.yaml` entry 設定 Ollama endpoint，以及該主機實際已安裝的模型 tag。若使用 repository 內參數化的 Mac / Surface pool，視設定需要在 `.env` 填入 `MAC_IP` 或 `SURFACE_IP` 等位址變數。
4. 執行 `khavis doctor` 檢查連線與模型是否存在，再用 `khavis doctor --deep` 執行一次真實最小生成請求。
5. 使用設定檔裡的 pool 名稱送出請求：

```bash
khavis chat --pool <configured-ollama-pool> "請把這段文字整理成三個重點。"
```

模型 tag 特意使用 placeholder：Ollama 只會提供選定主機已安裝的模型，而 repository 不保證某個固定模型名稱。

## 直接使用 CLI

`khavis chat` 不需要 daemon，就能透過 router 處理一個 prompt：

```bash
khavis chat "請解釋這個錯誤。"
khavis chat --capability <config-中的標籤> "請重構這個函式。"
khavis chat --pool <config-中的-pool-名稱> "請簡短回答。"
khavis chat --json "請回傳 provider 格式的結果。"
khavis chat --quiet "不要顯示 pool、model、token 尾列。"
khavis chat --max-attempts 2 "最多嘗試兩個 pool。"
```

預設情況下，CLI 會先刷新 pool 健康狀態；provider 失敗後，也可能在設定的 pool 中嘗試其他候選。這是有上限的 failover，不是保證：`--max-attempts`、provider 狀態、憑證、額度、網路與 capability 都會影響結果。只有在你理解可能選到失效 pool 時，才使用 `--no-refresh`。

常用維運指令：

```bash
khavis health --refresh   # 探測 pool，顯示 circuit / quota 狀態
khavis health --reset     # 探測前重設本程序的 circuit 狀態
```

Circuit 狀態屬於持有它的程序；獨立執行的 `khavis serve` 會有自己的 tracker。

## OpenAI 相容 HTTP API

先安裝 server extra、初始化設定，再啟動本機 server：

```bash
python -m pip install -e ".[server]"
khavis init
khavis serve --host 127.0.0.1 --port 8080
```

另開終端機呼叫 API：

```bash
curl -X POST http://127.0.0.1:8080/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{"messages":[{"role":"user","content":"用 Python 寫一個 quicksort。"}]}'
```

回應採用 OpenAI 風格的 `choices` / `model` / `usage` 結構，並可能附帶 `pool`、`cost_usd`、`latency_ms` 等 KHAVIS 欄位。啟用稽核記錄時，HTTP 請求會追加到 `config/audit.jsonl`。啟動 server 本身不會讓上游模型自動可用；provider 仍須完成設定。

## K.A.V.I.S. 與選用的多代理執行

安裝 `agents` extra 並設定圖譜所需的 provider 後，可以從 Python 呼叫選用的 LangGraph：

```bash
python -m pip install -e ".[agents]"
```

```python
from agents.graph import run_team

state = run_team("設計一個 todo app 的 REST API。", session_id="demo")
print(state.get("final"))
```

在部署文件中，`K.A.V.I.S.` 指的是這個內建 Agent 層，而不是某個 model pool。本地 graph 與遠端橋接器是兩條不同的執行路徑。啟用 Tencent Cloud / Hermes adapter 前，請先閱讀 [`deploy/README.md`](deploy/README.md)；adapter 預設只綁定 localhost，也可能要求 adapter token。`deploy/kavis_adapter.py` 會執行設定好的 Hermes executable，因此不是離線 mock，應只在可信任的環境啟動。

若要整合外部 coding CLI，請自行確認 executable 與登入狀態（例如使用該 CLI 的 `--version` 或登入流程）。KHAVIS 不會隨附或保證這些工具、其訂閱或預設模型別名。

## Provider plugin 與 pool 設定

Repository 內含多種 API 風格的 provider 實作，包括 OpenAI 相容 endpoint、Anthropic、Google 與 Ollama。Registry 會從 `providers/` 發現 plugin class；實際 pool 名稱、endpoint、模型 tag、憑證與 capability 標籤則由設定檔與環境變數提供。

建議先查看：

- [`config/pools.yaml`](config/pools.yaml)：pool 名稱、endpoint、模型與 `env_key`；
- [`config/capabilities.yaml`](config/capabilities.yaml)：capability 到 pool 的偏好；
- [`config/budgets.yaml`](config/budgets.yaml)：token 與成本政策；
- [`config/health.yaml`](config/health.yaml)：健康檢查與 circuit 政策；
- [`.env.example`](.env.example)：面向部署的環境變數範本；填入 secret 後不要提交到 Git。

最小的 capability routing 範例：

```python
from pathlib import Path

from core.capability_router import CapabilityRouter
from core.registry import PluginRegistry

root = Path(".")
registry = PluginRegistry(root).discover()
registry.apply_pools_config(root / "config" / "pools.yaml")
router = CapabilityRouter(registry)
router.load_capabilities(root / "config" / "capabilities.yaml")

# 請使用自己 config/capabilities.yaml 內存在的標籤。
plugin = router.select("<capability-tag>")
if plugin is None:
    raise RuntimeError("沒有可處理此 capability 的已設定 pool")
result = plugin.chat([{"role": "user", "content": "請重構這個函式。"}])
print(result["choices"][0]["message"]["content"])
```

每個 plugin 都回傳 OpenAI 風格的 dictionary。provider response 與 model identifier 仍由 provider 決定；回應中的 `model` 應視為該次請求實際回傳的值，不應視為長期可用性的保證。

## 選用整合

### Telegram bot

Telegram 整合是選用功能，需要自己的 token 與設定：

```bash
python -m pip install -e ".[bot]"
cp .env.example .env
# 依 docs/TELEGRAM_BOT.md 設定 BOT_TOKEN 與 ALLOWED_CHAT_IDS。
python -m bot.main --mode polling
```

Webhook 部署與維運細節請見 [`docs/TELEGRAM_BOT.md`](docs/TELEGRAM_BOT.md) 與 [`deploy/`](deploy/) 目錄。

### Docker 與 VM 部署

Docker、nginx、systemd 與 Tencent Cloud 說明獨立放置，讓本地 CLI setup 保持容易檢查：

- [`INSTALLATION.md`](INSTALLATION.md)：先決條件、安裝路徑、驗證與疑難排解；
- [`deploy/README.md`](deploy/README.md)：Ubuntu VM / Tencent Cloud 部署與服務；
- [`deploy/docker-compose.yml`](deploy/docker-compose.yml)：部署用 compose 檔；
- [`deploy/kavis_adapter.py`](deploy/kavis_adapter.py)：只綁 localhost 的 OpenAI 相容 K.A.V.I.S. bridge。

將服務暴露到網路前，請先閱讀部署檔案並自行確認安全設定。API key、adapter token 與 Telegram token 應放在本機 secret，不要提交到 Git。

## 文件入口

- [`INSTALLATION.md`](INSTALLATION.md)：安裝、驗證、執行與疑難排解。
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md)：YAML 與環境設定。
- [`docs/EVIDENCE.md`](docs/EVIDENCE.md)：功能宣稱與證據等級（E0–E3）。
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：router、registry、orchestration、memory 與 audit 概念。
- [`docs/HEALTH.md`](docs/HEALTH.md)：健康檢查、circuit 狀態與額度觀測。
- [`docs/PLUGIN_API.md`](docs/PLUGIN_API.md)：provider plugin 介面。
- [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md)：新增 provider plugin。
- [`docs/TELEGRAM_BOT.md`](docs/TELEGRAM_BOT.md)：選用 bot 的設定。
- [`deploy/README.md`](deploy/README.md)：VM 部署與 K.A.V.I.S. adapter。
- [`CONTRIBUTING.md`](CONTRIBUTING.md)：貢獻流程。
- [`CHANGELOG.md`](CHANGELOG.md)：版本與變更記錄。

本 README 刻意描述由設定檔決定的能力，不把特定 provider、模型、網頁、額度、延遲或外部 CLI 寫成普遍可用。使用任何整合前，請以 repository 實作與自己的 runtime 為準。

## Plugin 開發

Provider plugin 實作 `ProviderPlugin`，並回傳 OpenAI 風格的 response：

```python
from providers.base import ProviderPlugin


class MyProvider(ProviderPlugin):
    provider_id = "acme"

    def __init__(self, **kwargs):
        super().__init__(
            name="Acme-1",
            capabilities=["<capability-tag>"],
            **kwargs,
        )

    def chat(self, messages, **kwargs) -> dict:
        return {"choices": [], "model": "<provider-response>", "usage": {}}
```

在設定檔註冊 pool、補上適合 provider 的測試，並在提交 PR 前閱讀 [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md)。

## Roadmap

專案狀態：上方 status badge 保留自本次 repository snapshot。版本與功能狀態應以 [`CHANGELOG.md`](CHANGELOG.md)、實作與相關測試為準。選用的 dashboard、workbench 路徑、provider 整合與部署服務可能需要額外設定；本 README 不把它們寫成所有環境都已啟用的功能。

## 貢獻

歡迎回報 bug、提交 provider plugin、改善 routing 或文件。開始前請閱讀 [`CONTRIBUTING.md`](CONTRIBUTING.md)，不要把 secret 放進 Git，並在適當時更新 [`CHANGELOG.md`](CHANGELOG.md)。

## 社群與支援

- GitHub Issues：bug 回報與功能請求。
- GitHub Discussions：問題、實驗、routing 食譜與整合回饋。
- 安全性回報：請依 [`.github/SECURITY.md`](.github/SECURITY.md) 流程處理。

## 授權

Apache License 2.0。完整內容請見 [`LICENSE`](LICENSE)。

## 致謝

KHAVIS 建立在 [LiteLLM](https://github.com/BerriAI/litellm) 與 [OpenRouter](https://openrouter.ai) 社群的工作之上，也感謝被適配的 provider，以及每一位改善程式碼與文件的貢獻者。
