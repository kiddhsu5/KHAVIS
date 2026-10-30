# K.H.A.V.I.S. 操作手冊

本手冊涵蓋 K.H.A.V.I.S. Telegram dispatch bot 的**日常操作、部署、維運、疑難排解**。適合給操作者 (operator)、維運者 (SRE)、以及接手維護的開發者閱讀。

- **快速上手**：第 1 章
- **Telegram 指令**：第 2 章
- **架構速覽**：第 3 章
- **本地開發**：第 4 章
- **部署上雲 (阿里雲 ECS / 騰訊雲)**：第 5 章
- **設定參考**：第 6 章
- **疑難排解**：第 7 章
- **日常維運**：第 8 章
- **安全注意事項**：第 9 章

---

## 1. 快速上手 (5 分鐘)

### 1.1 我是使用者 (只要下指令)

1. 在 Telegram 搜尋 **`@KHAVISbot`**
2. 按 **Start** 或直接傳 `/start`
3. 收到 `👋 Welcome!` 歡迎訊息
4. 傳 `/run <你的 prompt>` 讓三個 AI backend 一起回答
5. 等 ⏳ 進度訊息逐段填入,最後跑出 🏁 consensus

範例:
```
/run 寫一個 Python function 計算 list 平均值
/run --only codex 簡述 LangGraph 的優缺點
/run --capability 程式碼 寫一個 quicksort
```

### 1.2 我是管理者 (要改設定、部署)

```bash
# 本地開發 (Mac 上)
cd /path/to/khavis
cp deploy/.env.example .env        # 填 BOT_TOKEN / ALLOWED_CHAT_IDS / API keys
python3 -m bot.main --mode polling # 長輪詢啟動 (最簡單,不需要公網)

# 雲端部署 (阿里雲 ECS)
bash deploy/scripts/deploy-aliyun.sh
```

詳見第 5 章。

---

## 2. Telegram 指令

所有指令都必須在已授權的 chat 中使用 (`ALLOWED_CHAT_IDS` 中有你的 chat ID)。

### 2.1 基本指令

| 指令 | 用途 | 範例 |
|---|---|---|
| `/start` | 歡迎訊息 + 顯示 chat_id | `/start` |
| `/help` | 顯示指令列表 | `/help` |
| `/status` | 每個 backend 健康狀態 | `/status` |
| `/pools` | 列出 K.H.A.V.I.S. 12 個池 + capabilities | `/pools` |

### 2.2 派工指令 `/run`

```
/run <prompt>                          # 派給 claude + codex + khavis 三個 backend
/run --only <A,B> <prompt>             # 只派給指定 subset
/run --capability <cap> <prompt>       # 傳 capability 給 khavis 池路由
```

**`--only` 可選值**:`claude` / `codex` / `khavis`(可逗號分隔)

**`--capability` 可選值** (對應 `config/capabilities.yaml`):
`中文` / `英文` / `程式碼` / `推理` / `工具調用` / `Embedding` / `長文` / `速度優先` / `辯論` / `審查` / `驗證`

### 2.3 範例

```
# 全部派工
/run 設計一個 REST API 給 todo app

# 只派給 codex
/run --only codex 簡述 Transformer 架構

# khavis 用中文能力池
/run --capability 中文 寫一段介紹

# 結合
/run --only khavis --capability 程式碼 寫一個實作 quicksort
```

### 2.4 回應格式

```
🔁 /run: 寫一個 Python function 計算 list 平均值

✅ claude (claude-haiku-4-5, 2.3s · in=245 out=88)
def average(nums):
    return sum(nums) / len(nums) if nums else 0

✅ codex (gpt-5-mini, 1.8s · in=240 out=65)
... (其他 backend 的答案)

✅ khavis (Ollama-Mac / gemma4:e2b, 8.4s)
... (答案)

🏁 consensus (from judge):
def average(nums): return sum(nums) / len(nums) ...
```

每個區塊顯示:
- ✅ / ❌ 成功或失敗
- backend 名 + model + latency + token 用量 (若 backend 有提供)
- 失敗會附 error message

最後 🏁 consensus 由 **本地 Ollama judge** (`qwen2.5:1.5b`) 挑選最佳答案。judge 失敗時自動 fallback 到「最長者勝」的 heuristic。

---

## 3. 架構速覽

```
Telegram (手機)
   ↓ webhook / long-poll
Caddy (TLS 終止, 阿里雲 VPS 上)   ← 自動 Let's Encrypt 或既有憑證
   ↓ :8080 (HTTP)
bot/main.py (FastAPI + uvicorn)
   ↓
handlers.py → dispatch.py → backends/
                              ├── claude.py        → `claude -p` (subprocess)
                              ├── codex.py         → `codex exec` (subprocess)
                              ├── khavis.py    → agents.run_team (in-process)
                              └── judge.py         → Ollama qwen2.5:1.5b (本地 HTTP)
   ↓
aggregator.py → format_report() → Telegram sendMessage
```

**核心設計**:
- **Fan-out**: 三個 backend **並行** 執行 (`asyncio.as_completed`),誰先完成誰先 edit 進度訊息
- **Streaming edits**: 佔位訊息 "⏳ dispatching..." 隨 backend 完成而編輯 (≤ 1 次/秒,避免 Telegram rate limit)
- **Judge**: 全部完成後呼叫 Ollama 挑最佳答案,失敗 fallback 到 longest-wins heuristic
- **多 agent pipeline** (`agents.run_team`): 內部走 `planner → coder_a + coder_b → debate → critic → verify → learn` 的 LangGraph 圖

---

## 4. 本地開發

### 4.1 需求

- macOS / Linux
- Python **3.12+** (系統預設 Python 3.9 不行)
- Homebrew Python: `/opt/homebrew/bin/python3.12`
- Ollama (在跑或至少已安裝)
- `claude` CLI (給 claude backend 用)
- `codex` CLI (給 codex backend 用,來自 ChatGPT.app)

### 4.2 安裝

```bash
cd /path/to/khavis

# 安裝依賴
/opt/homebrew/bin/python3.12 -m pip install --break-system-packages \
    -e ".[bot,dev]"

# 建 .env (從範本)
cp deploy/.env.example .env
# 用編輯器填入:
#   BOT_TOKEN (從 @BotFather)
#   ALLOWED_CHAT_IDS (你的 chat ID, 逗號分隔)
#   12 個 LLM API keys (MiniMax/GLM/Google/NVIDIA/ByteDance/OpenRouter/OpenAI/Anthropic/...)
```

### 4.3 跑測試

```bash
/opt/homebrew/bin/python3.12 -m pytest --tb=no
# 201 passed

# 只跑 bot 測試
/opt/homebrew/bin/python3.12 -m pytest tests/test_bot_*.py -v

# Lint + 類型
ruff check providers/ core/ scripts/ tests/ bot/
ruff format --check providers/ core/ scripts/ tests/ bot/
/opt/homebrew/bin/python3.12 -m mypy providers/ core/ bot/ --ignore-missing-imports
```

### 4.4 跑 bot (本地)

**方式 A — 長輪詢 (最簡單)**

```bash
/opt/homebrew/bin/python3.12 -m bot.main --mode polling
```

不需要公網、不需要憑證。直接收 Telegram 訊息。

**方式 B — Webhook (需要公網 HTTPS)**

```bash
# 暫時用 ngrok
ngrok http 8080
# 看輸出的 https://xyz.ngrok.io,再
BOT_TOKEN=... WEBHOOK_SECRET=... ALLOWED_CHAT_IDS=... python3 -m bot.main --mode webhook --port 8080

# 設定 Telegram webhook
curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" -d "url=https://xyz.ngrok.io/webhook" -d "secret_token=${WEBHOOK_SECRET}"
```

**方式 C — 背景 daemon (生產推薦)**

```bash
.venv/bin/python scripts/launch_bot_daemon.py

# 查看
tail -f ~/.cache/khavis-bot/bot.log
cat ~/.cache/khavis-bot/bot.pid

# 停止
.venv/bin/python scripts/launch_bot_daemon.py --stop
```

---

## 5. 部署上雲 (阿里雲 ECS)

### 5.1 事前準備

| 準備 | 怎麼做 |
|---|---|
| **VPS** | 阿里雲控制台 → 雲伺服器 ECS (輕量應用伺服器 Lighthouse 亦可) |
| 規格 | 1 vCPU + 1 GB RAM 足夠 (輕量 bot 用量) |
| 作業系統 | Ubuntu 22.04 / Container-Optimized OS (已預裝 Docker) |
| **SSH 金鑰** | 控制台 → Key Pairs → 產生 → 綁到 instance |
| **防火牆 / 安全組** | 開 port: `22` (SSH), `80` (ACME HTTP-01), `443` (Telegram webhook) |
| **DNS** | 在您的 DNS 服務商加 A record: `bot.example.com → <ECS 公有 IP>` |

### 5.2 部署流程

```bash
# === 步驟 1: 準備 .env (本機) ===
cd /path/to/khavis
cp deploy/.env.example .env
# 填入 BOT_TOKEN / ALLOWED_CHAT_IDS / 12 個 LLM API keys

# === 步驟 2: 上傳 .env 到 ECS (出頻外, 不進 git) ===
scp .env root@<ECS_IP>:~/khavis-bot/.env

# === 步驟 3: 執行一鍵部署 ===
ALIYUN_ECS_HOST=root@<ECS_IP> bash deploy/scripts/deploy-aliyun.sh
```

腳本會自動:
1. 在 Mac 上 `docker build` 建 image
2. `docker push` 推到 Aliyun Container Registry (ACR)
3. `ssh` 到 ECS → `git clone` / `git pull` → `docker pull` → `docker compose up -d`
4. 驗證 `https://bot.example.com/healthz` 返回 `{"status":"ok",...}`

### 5.3 設定 Telegram Webhook (一次性)

```bash
curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" -d "url=https://bot.example.com/webhook" -d "secret_token=${WEBHOOK_SECRET}"

# 驗證
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```

### 5.4 驗收

```bash
# 健康檢查
curl -fsS https://bot.example.com/healthz | jq

# Telegram 應該回應
# (在 Telegram 傳 /start 給 @KHAVISbot)
```

---

## 6. 設定參考

### 6.1 `.env` 環境變數

| 變數 | 必要 | 預設 | 用途 |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | Telegram bot token (@BotFather 給的) |
| `WEBHOOK_SECRET` | ✅ (webhook 模式) | 無(不再預設 BOT_TOKEN) | Telegram webhook 驗證 header;1-256 字元 `[A-Za-z0-9_-]`,須與 setWebhook 的 `secret_token` 相同;polling 模式不需要 |
| `ALLOWED_CHAT_IDS` | ✅ | (空 = 拒絕所有人) | 允許使用 bot 的 chat ID,逗號分隔 |
| `BOT_MODE` | ⭕ | `webhook` | `webhook` (公網) 或 `polling` (本地) |
| `BOT_HOST` | ⭕ | `0.0.0.0` | Webhook bind host |
| `BOT_PORT` | ⭕ | `8080` | Webhook bind port |
| `BACKEND_TIMEOUT_S` | ⭕ | `180` | 每個 backend 的硬逾時 |
| `LONG_POLL_TIMEOUT_S` | ⭕ | `30` | Telegram getUpdates timeout |
| `CLAUDE_DISPATCH_MODEL` | ⭕ | (CLI 預設) | 傳 `--model` 給 claude |
| `CODEX_DISPATCH_MODEL` | ⭕ | (CLI 預設) | 傳 `-m` 給 codex |
| `SURFACE_IP` | ⭕ | — | Surface Ollama LAN IP (`Ollama-Surface` 池) |
| `MiniMax_API_KEY` / `ZHIPUAI_API_KEY` / `GOOGLE_API_KEY` / `NVIDIA_API_KEY` / `BYTEDANCE_API_KEY` / `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | 給用到的池 | — | 各 LLM 廠商 API keys |

### 6.2 LLM 池對應

`config/pools.yaml` 定義 12 個池的 model + endpoint。改檔後**不需重啟** (每次 chat 重讀)。

| 池 | 廠商 | Model (預設) |
|---|---|---|
| `MiniMax-M3` | MiniMax | `MiniMax-M3` |
| `GLM-5.3` | ZhipuAI | `glm-5.3` |
| `Google-Gemini` | Google | `gemini-flash-latest` / `gemini-pro-latest` |
| `NVIDIA-Cloud` | NVIDIA NIM | (官方預設) |
| `DeepSeek-V4-Pro` | ByteDance Volcano Ark | `deepseek-v4-pro` |
| `DeepSeek-V4.1-Flash` | ByteDance Volcano Ark | `deepseek-v4.1-flash` |
| `GLM-5.3-Flash` | ByteDance Volcano Ark | `glm-5.3-flash` |
| `OpenRouter-Free` | OpenRouter | `openai/auto` |
| `OpenAI-API` | OpenAI Platform | `gpt-5-mini` |
| `Claude-API` | Anthropic | `claude-haiku-4-5-20251001` |
| `Ollama-Mac` | 本機 Ollama | `gemma4:e2b` |
| `Ollama-Surface` | Surface Ollama | `qwen2.5:1.5b` |

### 6.3 Capability → 池映射

`config/capabilities.yaml` 定義每個 capability tag 可用哪些池。

12 個 capability: `中文` `英文` `程式碼` `推理` `工具調用` `Embedding` `長文` `速度優先` `辯論` `審查` `驗證`

`/run --capability X` 指令可以挑指定 capability 派工。

---

## 7. 疑難排解

| 症狀 | 可能原因 | 解決 |
|---|---|---|
| Telegram 送訊息 bot 不回應 | (1) bot daemon 沒跑 (2) chat_id 不在 allowlist (3) token 過期 | `tail -f ~/.cache/khavis-bot/bot.log` 看有無 `incoming chat_id=...` 訊息 |
| `incoming chat_id=X` 但無回應 | `X` 不在 `ALLOWED_CHAT_IDS` | 改 `.env` 加入 X,重啟 bot |
| Bot 回應 HTTP 400 "message to be replied not found" | 用了 `update_id` 而非 `message_id` | 已修 (`bot/message_id` commit `7b6fe4b`) |
| Bot 回應 HTTP 400 "unsupported parse_mode" | `parse_mode=null` 被 Telegram 拒 | 已修 (`telegram_api.py` 濾掉 None 值, commit `463f839`) |
| `/run` 一直顯示 "⏳ dispatching..." 不完成 | 某 backend 卡住 (通常是 CLI 找不到) | 看 log,若某 backend 超過 `BACKEND_TIMEOUT_S` 會自動 kill |
| `judge model failed` 警告 | Ollama judge 不通 | 確認本機 Ollama 在跑, `qwen2.5:1.5b` 已 pull |
| `401 unauthorized` 錯誤 (來自 LLM 池) | API key 是 placeholder 或過期 | 確認 `.env` 中各 key 是真實值 |
| `429 rate limited` 錯誤 | 某池觸發 rate limit | 等待;長的 prompt 會自動 fallback 到其他池 |
| Bot daemon 啟動就退 | `.env` 路徑錯、Python 版本錯 | 看 `~/.cache/khavis-bot/bot.log`,確認 launcher 用的是專案 .venv(預設會自動選) |
| `~/.cache/khavis-bot/bot.pid` 存在但進程不在 | 上次異常退出殘留 | `.venv/bin/python scripts/launch_bot_daemon.py --stop` 一次再啟動 |
| 阿里雲部署後 404 / 502 | (1) DNS 未生效 (2) Caddy 無法 reach bot | `docker compose logs caddy` 看 TLS 取憑證; `docker compose logs bot` 看 app 啟動 |
| ACME TLS 取憑證失敗 | inbound 80 被擋 | 開阿里雲安全組 port 80 |
| 改 `.env` 沒生效 | 有些變數要重啟才生效 | `.venv/bin/python scripts/launch_bot_daemon.py --stop && .venv/bin/python scripts/launch_bot_daemon.py` |

### 7.1 看 log

```bash
# 本機 (polling mode)
tail -f ~/.cache/khavis-bot/bot.log

# 本機 (daemon)
tail -f ~/.cache/khavis-bot/bot.log

# 雲端
ssh root@<ECS_IP> "cd ~/khavis-bot && docker compose logs -f bot"
ssh root@<ECS_IP> "cd ~/khavis-bot && docker compose logs -f caddy"
```

### 7.2 健康檢查

```bash
# 本機
curl -fsS http://localhost:8080/healthz | jq

# 雲端
curl -fsS https://bot.example.com/healthz | jq
```

回應範例:
```json
{
  "status": "ok",
  "backends": {
    "claude": {"ok": "yes", "detail": "claude 2.1.273"},
    "codex": {"ok": "yes", "detail": "codex 0.155.0"},
    "khavis": {"ok": "yes", "detail": "12 pools registered"}
  }
}
```

---

## 8. 日常維運

### 8.1 重啟 bot

```bash
# 本機
.venv/bin/python scripts/launch_bot_daemon.py --stop
.venv/bin/python scripts/launch_bot_daemon.py

# 雲端
ssh root@<ECS_IP> "cd ~/khavis-bot && docker compose restart bot"
```

### 8.2 更新程式碼

```bash
# 本機
git pull
.venv/bin/python scripts/launch_bot_daemon.py --stop
.venv/bin/python scripts/launch_bot_daemon.py

# 雲端
bash deploy/scripts/deploy-aliyun.sh
```

### 8.3 更新 LLM API keys

1. 改 `.env`
2. 重啟 bot (本機) 或重 build + rollout (雲端)

### 8.4 更新 Telegram bot token

1. 到 @BotFather 重設 token
2. 改 `.env` 的 `BOT_TOKEN` (與 `WEBHOOK_SECRET` 一起改)
3. 重啟 bot
4. 重新設 webhook: `curl ".../setWebhook" -d "url=..." -d "secret_token=${WEBHOOK_SECRET}"`

### 8.5 加新使用者

1. 讓新使用者從 Telegram 對 `@KHAVISbot` 傳 `/start`
2. 從 `tail -f ~/.cache/khavis-bot/bot.log` 撈出 `chat_id=X`
3. 把 X 加入 `.env` 的 `ALLOWED_CHAT_IDS` (逗號分隔)
4. 重啟 bot

### 8.6 新增 LLM 池

1. 在 `providers/` 加新 plugin (繼承 `ProviderPlugin`)
2. 在 `config/pools.yaml` 加池定義
3. 在 `config/capabilities.yaml` 將池加入相關 capability
4. 重啟 bot (registry 是啟動時掃描)

詳見 `docs/PLUGIN_DEVELOPMENT.md`。

### 8.7 備份與還原

```bash
# 備份 .env (機密!)
cp .env ~/backup/khavis-$(date +%Y%m%d).env.enc
gpg -c ~/backup/khavis-$(date +%Y%m%d).env.enc

# 備份對話 history (SQLite, agents/memory.py 寫入)
cp memory/episodic.sqlite ~/backup/
```

---

## 9. 安全注意事項

### 9.1 機密保護

| 項目 | 做法 |
|---|---|
| `.env` | 絕不 commit; `.gitignore` 已擋 |
| `BOT_TOKEN` | 只從環境變數讀;從 @BotFather 重設就換 |
| `WEBHOOK_SECRET` | webhook 模式必填(空值或不合法字元啟動即報錯),不再沿用 BOT_TOKEN;`hmac.compare_digest` 常數時間比較 |
| `ALLOWED_CHAT_IDS` | 空白 = 拒絕所有人 (no permissive default) |
| LLM API keys | 都在 `.env`,不 commit |
| TLS 憑證 | `deploy/certs/*.pem` 已 .gitignore |

### 9.2 網路安全

- Webhook 用 HTTPS (Caddy 自動 TLS)
- Bot container 只 bind `127.0.0.1:8080`,Caddy 作反代
- 只對外開 22 (SSH), 80 (ACME), 443 (webhook)

### 9.3 存取控制

- 允許清單 (`ALLOWED_CHAT_IDS`) 是**唯一**的存取控制
- 沒有 role-based 或 rate limit 進階功能
- 想要更嚴格可搭配 `iptables` / 雲端安全組

### 9.4 資安事件應變

如果 BOT_TOKEN 洩漏:
1. 到 @BotFather 立即 `/revoke`
2. 改 `.env` 的 `BOT_TOKEN` + `WEBHOOK_SECRET`
3. 重啟 bot
4. 重設 webhook: `curl ".../setWebhook" -d "url=..." -d "secret_token=${WEBHOOK_SECRET}"`

如果 LLM API key 洩漏:
1. 到該廠商 console 重設 key
2. 改 `.env` 對應 key
3. 重啟 bot

---

## 附錄 A: 常用指令速查

```bash
# 本地開發
python3 -m pytest --tb=no                                          # 跑全部測試
python3 -m bot.main --mode polling                                 # 跑 bot (長輪詢)
.venv/bin/python scripts/launch_bot_daemon.py                              # 啟動 bot daemon
.venv/bin/python scripts/launch_bot_daemon.py --stop                       # 停止 bot daemon
tail -f ~/.cache/khavis-bot/bot.log                                              # 看 bot log

# 檢查
ruff check providers/ core/ scripts/ tests/ bot/                   # Lint
ruff format --check providers/ core/ scripts/ tests/ bot/          # Format 檢查
python3 -m mypy providers/ core/ bot/ --ignore-missing-imports     # 類型檢查

# Telegram API 直接查
curl "https://api.telegram.org/bot${BOT_TOKEN}/getMe"
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"

# 部署
ALIYUN_ECS_HOST=root@<IP> bash deploy/scripts/deploy-aliyun.sh     # 一鍵部署
```

## 附錄 B: 檔案結構

```
/path/to/khavis/
├── bot/                          # Telegram dispatch bot
│   ├── __init__.py
│   ├── __main__.py
│   ├── main.py                   # FastAPI app + lifespan
│   ├── handlers.py               # /start /help /status /pools /run
│   ├── dispatch.py               # fan-out orchestrator
│   ├── aggregator.py             # format_report
│   ├── telegram_api.py           # sendMessage/editMessageText
│   ├── polling.py                # getUpdates 長輪詢
│   ├── webhook.py                # POST /webhook
│   ├── secrets.py                # BotSecrets dataclass
│   ├── models.py                 # IncomingMessage / DispatchEnvelope / ...
│   ├── pairing_loader.py         # 讀歷史 telegram-pairing.json
│   └── backends/
│       ├── base.py               # Backend ABC
│       ├── claude.py             # `claude -p` subprocess
│       ├── codex.py              # `codex exec` subprocess
│       ├── khavis.py         # agents.run_team (in-process)
│       └── judge.py              # Ollama qwen2.5:1.5b 當 judge
├── core/                         # registry + capability_router
├── providers/                    # 12 個 LLM provider plugins
├── agents/                       # 多 agent pipeline (LangGraph)
├── config/
│   ├── pools.yaml                # 12 個池的 model + endpoint
│   ├── capabilities.yaml         # capability → 池映射
│   └── agents.yaml               # Role 設定
├── deploy/
│   ├── Dockerfile                # 多階段 image
│   ├── Caddyfile                 # TLS 終止
│   ├── docker-compose.yml        # caddy + bot
│   ├── entrypoint.sh
│   ├── README.md                 # 部署手冊
│   └── scripts/
│       ├── deploy-aliyun.sh
│       └── deploy-tencentcloud.sh
├── scripts/
│   ├── launch_bot_daemon.py      # POSIX double-fork daemon launcher
│   ├── setup_ollama.sh           # Mac + Surface Ollama 一鍵設定
│   └── setup_surface_windows.ps1 # Surface 端 PowerShell 設定
├── tests/                        # 201 pytest tests
├── docs/
│   ├── OPERATIONS.md             # (本檔)
│   ├── TELEGRAM_BOT.md           # bot 架構說明
│   ├── ARCHITECTURE.md
│   ├── PLUGIN_DEVELOPMENT.md
│   ├── CONFIGURATION.md
│   └── FAQ.md
├── .env                          # 機密 (絕不 commit)
├── .env.example                  # 範本
└── pyproject.toml
```

---

**最後更新**: 2026-09-22
**適用版本**: v0.1.0-alpha + Unreleased
**授權**: Apache-2.0
