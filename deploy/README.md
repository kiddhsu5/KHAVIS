# K.H.A.V.I.S. 部署指南（Windows / macOS / Linux / 雲端 VM）

> 一鍵安裝：`bash deploy/install.sh`（macOS / Linux）或 `.\deploy\install.ps1`（Windows）。
> 目標：整個專案在自己的機器或雲端 VM 上運行；開發與運行可以分離。

## 架構總覽

```
瀏覽器 ── https ──► （選用）nginx / 反向代理 ──► K.H.A.V.I.S. Workbench（127.0.0.1:8080）
                                                  │
                                                  ├─ K.A.V.I.S. 內建 agent
                                                  │   ├─ 本地：LangGraph 7 角色團隊協同
                                                  │   └─ 遠端：kavis-adapter（127.0.0.1:18081，Unix 選配）
                                                  │            └─ hermes -z（openclaw × hermes 環境）
                                                  ├─ 選用 Telegram bot
                                                  └─ 供應商 API pools（.env keys）
```

**K.A.V.I.S.** 是項目內建的協同 agent：對話中勾選即調度本地 LangGraph 團隊
（Planner / Coder ×2 / Debater / Critic / Verifier / Learner）。安裝
`kavis-adapter` 後，遠端 openclaw × hermes 節點可成為同一 agent 的雲端執行點。

## 一鍵安裝

```bash
# macOS / Linux
bash deploy/install.sh

# 含 K.A.V.I.S. 遠端派工橋接器（需要已安裝 hermes 的 Unix 環境）
bash deploy/install.sh --with-adapter

# Windows（PowerShell，於 repo 根目錄）
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\install.ps1
```

安裝器行為：環境檢查（Python ≥ 3.11、git）→ clone repo → venv + 依賴 →
`.env` 初始化（**已存在不覆蓋**）→ 常駐服務（可選）→ 健康檢查。

常用參數：

| 參數 | 說明 |
| --- | --- |
| `--home <path>` / `-Home` | 安裝目錄（預設 `~/khavis`） |
| `--port <n>` / `-Port` | Workbench 埠（預設 8080） |
| `--with-adapter` | 安裝 K.A.V.I.S. 遠端派工橋接器（Unix + hermes） |
| `--no-service` | 不註冊常駐服務／排程，只給啟動指令 |
| `--skip-clone` | 略過 clone，只更新依賴與設定 |

## 既有非 git 部署的遷移

若目標目錄已存在且不是 git repo，安裝器會先搬到 `<home>-preinstall-<時間戳>`
再 clone。之後手動回復運行資料：

```bash
OLD=<home>-preinstall-<時間戳>
cp "$OLD/.env" <home>/.env
cp "$OLD/config/workbench_catalog.json" <home>/config/ 2>/dev/null || true
cp -r "$OLD/workspace" <home>/ 2>/dev/null || true
cp -r "$OLD/logs" <home>/ 2>/dev/null || true
```

## 日常維運

| 操作 | 指令 |
| --- | --- |
| 升級版本 | `git -C <home> pull` 後重啟服務 |
| 狀態／日誌（Linux） | `systemctl status khavis-workbench` / `journalctl -u khavis-workbench -n 50` |
| 重啟（Linux） | `sudo systemctl restart khavis-workbench` |
| 手動啟動 | `<home>/.venv/bin/python <home>/scripts/status_server.py --port 8080` |
| K.A.V.I.S. 橋接器 | `systemctl --user status kavis-adapter`（Unix） |
| 橋接器健康 | `curl -s http://127.0.0.1:18081/healthz` |

Windows：以「工作排程器」管理 `KHAVIS-Workbench` 排程，或直接手動啟動。

## 對外服務（選用反向代理）

Workbench 預設只聽 `0.0.0.0:8080`（安裝器建立的服務）；若要對外，請以 nginx
或 Caddy 反代並上 TLS，且**務必**在 `.env` 設定 `KHAVIS_API_KEY`（寫操作令牌）。

```nginx
server {
    listen 443 ssl;
    server_name khavis.example.com;
    ssl_certificate     /etc/ssl/certs/khavis.pem;
    ssl_certificate_key /etc/ssl/private/khavis.key;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_read_timeout 300s;   # 多 Agent 協同可能超過 60s
    }
}
```

## K.A.V.I.S. 遠端派工橋接器（Unix 選配）

`deploy/kavis_adapter.py`：OpenAI 相容橋接器，把
`POST /v1/chat/completions` 轉為 `hermes -z` 單次無頭任務。

- 只綁 `127.0.0.1:18081`；全域鎖序列化，避免與 gateway session 互踩。
- 可選 bearer token：設 `KAVIS_ADAPTER_TOKEN` 後，Workbench `.env` 的
  `KAVIS_TENCENT_API_KEY` 需同值。
- Workbench 端以 `KAVIS_TENCENT_DISPATCH_URL` 接上（`--with-adapter` 會自動寫入）。
- `KAVIS_TENCENT_ENDPOINT`：遠端節點健康探測位址；未設定時顯示未配置。
- 它會執行設定好的 hermes 執行檔，**不是離線 mock**——只在可信任環境啟動。

## 環境變數

見 [`.env.example`](../.env.example)。重點：

- 供應商 keys（BYOK，各自向供應商計費）
- `KHAVIS_API_KEY`：Workbench 寫操作令牌（對外服務強烈建議）
- `KAVIS_TENCENT_DISPATCH_URL` / `KAVIS_TENCENT_API_KEY` / `KAVIS_TENCENT_MODEL`
- `KAVIS_ADAPTER_TOKEN` / `KAVIS_ADAPTER_TIMEOUT_S`（橋接器自身）

秘密一律放本機，**不要提交到 Git**。
