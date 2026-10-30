# Edge failover：Mac + Surface LAN 雙機 Ollama 模式

> K.H.A.V.I.S. 把「同一個家裡兩台機器各跑 Ollama」做成 first-class 設定。
> 對手（LiteLLM / Bifrost / Portkey）都需要把 local model 跑在 gateway 同機；
> 這份文件示範怎麼在 K.H.A.V.I.S. 裡跨機 failover。

最後更新：2026-09-24

## 1. 為什麼需要

homelab / 個人開發者常見的痛：

- **大模型很重**：`gemma4:e2b` 跑在 M3 Max 上很順，但同樣的模型跑在 i5/8GB
  的 Surface 上根本起不來。
- **省電**：Surface 待機功耗低，當備援機比讓 Mac 24 小時開著划算。
- **網路瞬斷**：Mac 在書房、Surface 在客廳，Wi-Fi roaming 偶爾斷一下。
- **單機失敗**：Mac 升級 OS 重開機，Surface 立刻頂上，agent 流程不中斷。

K.H.A.V.I.S. 的解法：`Ollama-Mac` + `Ollama-Surface` 是兩個獨立 pool，capability
路由看哪台活著、哪台有能力就派給誰。

## 2. 設定流程（macOS + Windows）

### 2.1 Mac 端（主）

1. 安裝 Ollama：
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```
2. 啟動 daemon（macOS 用 launchd 自動啟動即可）。
3. 跑 K.H.A.V.I.S. 提供的 setup 腳本：
   ```bash
   bash scripts/setup_ollama.sh --mac
   ```
   這個腳本會做：
   - 確認 `ollama` CLI 在 PATH
   - 探測 `http://localhost:11434/api/tags` 端點
   - 自動 pull 缺的 model（預設 `gemma4:e2b`）
   - 跑一次 1-token smoke test，確認 daemon 真的能回應

### 2.2 Surface 端（備援，跑小模型）

> Surface 跑 i5 / 8GB，適合 `qwen2.5:1.5b` 之類的輕量模型。

1. 安裝 Ollama for Windows（從 ollama.com 下載）。
2. **以系統管理員身分**跑 PowerShell 腳本：
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_surface_windows.ps1
   ```
   這個腳本會做：
   - 設定 `OLLAMA_HOST=0.0.0.0`（系統環境變數，等同 setx /m），讓 daemon 監聽 LAN
   - 開 Windows Firewall TCP 11434（inbound + outbound）
   - 註冊 Ollama 開機自動啟動
   - 預 pull `qwen2.5:1.5b`
3. 取得 Surface 的 LAN IP（例如 `192.168.1.42`）。

### 2.3 K.H.A.V.I.S. 端

在 `.env` 寫入 Surface IP：

```bash
SURFACE_IP=192.168.1.42
```

跑 setup 腳本驗證兩台都通：

```bash
bash scripts/setup_ollama.sh
# 會自動跑 --mac + --surface（從 .env 讀 SURFACE_IP）
```

成功時輸出：

```
== Ollama-Mac @ http://localhost:11434 ==
  ✓ ollama CLI present (ollama version 0.x.x)
  ✓ endpoint reachable: http://localhost:11434
  ✓ model present:    gemma4:e2b
  ✓ smoke test:       gemma4:e2b

== Ollama-Surface (target 192.168.1.42) ==
  ✓ endpoint reachable: http://192.168.1.42:11434
  ✓ wrote SURFACE_IP=192.168.1.42 to /path/to/.env
  ✓ model present:    qwen2.5:1.5b
  ✓ smoke test:       qwen2.5:1.5b

all Ollama targets ready.
```

## 3. 容錯行為

兩台 Ollama 都被宣告成獨立的 pool，capability 路由看到的是「`Ollama-Mac` 跟
`Ollama-Surface` 都能回應長 prompt / 短 prompt」：

| 狀況 | K.H.A.V.I.S. 行為 |
| --- | --- |
| Mac 上線，Surface 離線 | 流量全部走 `Ollama-Mac` |
| Mac 離線（OS upgrade、睡眠），Surface 上線 | 流量自動切到 `Ollama-Surface` |
| 兩台都離線 | capability 路由 fallback 到雲端 pool（Gemini / OpenRouter / ...） |
| Mac 回來但網路瞬斷 | 一次失敗（`chat()` 回 error），下一次請求重新選 — 不會卡住 |

具體機制：
- capability 路由在 weighted random 之前，會先用 `health_check()` 過濾掉沒回應的 pool
- 健康檢查失敗只會讓該次請求改走別的 pool，不會 raise 整個請求
- 上線偵測是 lazy：每次 `select()` 都重新確認，所以 Mac 睡醒後下一輪自動被選回

## 4. 加上 Wireguard / Tailscale（跨網段 / 出國使用）

> 這段是真正讓「edge failover」成故事的關鍵：Mac 在家、Surface 在辦公室，
> 中間用 mesh VPN 串起來。

### 4.1 為什麼需要 VPN

- Ollama 預設沒加密；走 public Wi-Fi 不安全
- 跨網段（家裡 192.168.1.x、辦公室 192.168.10.x）需要 overlay 網路
- ISP CGNAT 後直接 TCP 連不到對方

### 4.2 推薦：Tailscale（最簡）

1. 兩台機器都裝 Tailscale，login 同一個 account
2. 拿到 Tailscale IP（例如 `100.x.y.z`）
3. 改 `.env`：
   ```bash
   SURFACE_IP=100.x.y.z   # 不再是 LAN IP
   ```
4. `setup_ollama.sh` 跑一次即可 — 腳本只看 `SURFACE_IP`，不假設是 LAN

### 4.3 進階：Wireguard

如果需要更精細的控制：

- Wireguard 比 Tailscale 難設定，但 throughput 較高
- Mac 跟 Surface 各自起 Wireguard peer，互相 allow 11434
- 把對端的 Wireguard IP 寫進 `SURFACE_IP`

## 5. 怎麼驗證 failover 有真的運作

### 5.1 健康檢查

```bash
python -c "
from core.registry import PluginRegistry
from core.capability_router import CapabilityRouter
from pathlib import Path
r = PluginRegistry().discover()
cap = CapabilityRouter(r).load_capabilities(Path('config/capabilities.yaml'))
for p in cap.candidates('速度優先'):
    print(p.name, '->', 'OK' if p.health_check() else 'DOWN')
"
```

預期：Mac 跟 Surface 都顯示 `OK`；把其中一台 Ollama daemon 關掉，重跑會看到
對應的那條變 `DOWN`，但其他 pool 不受影響。

### 5.2 整合測試

```bash
python scripts/integration_test.py --verbose
```

`integration_test.py` 會 ping 每個 pool；本地 Ollama 兩個池都會跑，
可以用來驗證 `Ollama-Mac` 跟 `Ollama-Surface` 都列在 healthy pool 清單。

## 6. 已知限制

| 限制 | 解法 |
| --- | --- |
| 兩個 Ollama daemon 不能跑同一個大模型（VRAM 不夠） | Mac 跑 `gemma4:e2b`、Surface 跑 `qwen2.5:1.5b`，capability 標籤分流 |
| Surface 連回家裡 Mac 要走 VPN | 用 Tailscale（見 §4.2） |
| failover 不持久化（沒有服務發現協議） | 每次 `select()` 都做 health check；這是設計取捨，換來零設定成本 |
| 同時掛掉的話雲端 fallback 要有 API key | `.env.example` 有完整 list |

## 7. 跟策略的對應

- **白地帶 §5.1「跨境 / 多地域 quota 容錯」**：這份文件就是那條白地帶的落地實例
- **護城河 §5.2「YAML-first + hot reload」**：failover 設定全在 `config/pools.yaml`，
  加一台機器只需要新增一個 pool 條目
- **借鑑點 §6「Wireguard / Tailscale 案例」**：§4 就是這條的完整內容

未來 roadmap：
- 自動偵測 macOS sleep / wake 事件，把 pool 暫時標記為「低優先」
- 加 `mesh` discovery：自動掃 LAN 找其他跑 Ollama 的機器
- 把「edge failover」做成 `khavis edge` 子命令，自動 Wireguard 設定

## 8. 參考

- `scripts/setup_ollama.sh` — 兩個 host 通用 setup
- `scripts/setup_surface_windows.ps1` — Surface 端 OLLAMA_HOST 設定
- `config/pools.yaml` — `Ollama-Mac` / `Ollama-Surface` 兩個 pool 條目
- `config/capabilities.yaml` — 哪些 capability 會派到這兩個 pool
- `docs/STRATEGY.md` §5.1 — 白地帶分析
