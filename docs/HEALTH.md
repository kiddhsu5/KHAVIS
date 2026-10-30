# Health、熔斷與配額感知路由

> 這份文件描述 K.H.A.V.I.S. 什麼時候**不再把請求送去某個 pool**，
> 以及為什麼這樣做才能讓 README 承諾的 *zero quota interruption* 成立。

最後更新：2026-09-30 · 對應 `core/health.py`、`config/health.yaml`

---

## 1. 為什麼需要這一層

每個 provider plugin 從一開始就實作了兩個方法：

```python
plugin.health_check()  # -> {"ok": bool, "detail": str}
plugin.check_quota()  # -> {"remaining": int|None, "total": int|None, "tier": str|None}
```

但在你看到這份文件之前，**整個 `core/` 從來沒有呼叫過它們**。
`CapabilityRouter.select()` 只做兩件事：比對 capability tag、加權隨機挑一個。
結果是：一個已經連續回 429 十幾分鐘的 pool，路由器還是會持續把請求送過去。

`tests/test_capability_router.py` 裡甚至有一個測試把這個行為**當成預期寫進斷言**：

```python
# 舊版：註解很誠實，但斷言方向是錯的
selected = router.select("any-cap")
assert selected is failing  # 壞掉的 pool 還是被選中
```

現在那段斷言已經反轉，健康過濾由 `core/health.py` 負責。

---

## 2. 五個狀態

```
UNKNOWN ──首次探測成功──► CLOSED ◄──探測成功──┐
   │                       │                   │
   │                  連續失敗 ≥ 門檻            │ 冷卻結束後探測成功
   │                       ▼                   │
   └──────────────────►  OPEN ──冷卻到了──► HALF_OPEN ┘
                                │
                          冷卻未到 ──► (不可用)

任何狀態 ──遇到配額錯誤──► EXHAUSTED ──配額恢復/refresh──► CLOSED
```

| 狀態 | 意義 | 收到流量？ | 怎麼恢復 |
|---|---|---|---|
| `UNKNOWN` | 還沒探測過 | ✅ | 首次 `refresh()` 後轉移 |
| `CLOSED` | 健康 | ✅ | — |
| `OPEN` | 熔斷中（連續失敗） | ❌ | 冷卻結束 → `HALF_OPEN` |
| `HALF_OPEN` | 放一個探測進去 | ✅（僅一次） | 成功 → `CLOSED`／再失敗 → `OPEN` |
| `EXHAUSTED` | 配額用完 | ❌ | 下次 `refresh()` 看到配額回來 |

**為什麼分開 `OPEN` 和 `EXHAUSTED`？**

兩者的復原條件完全不同。配額用完的 pool **本身完全健康**，它只是沒東西可給了 ——
頂多在 3 分鐘後視窗重置就自動恢復。熔斷則是因為連續收到真實錯誤（連線失敗、
5xx、認證失敗），那個 pool 可能壞掉，需要時間和探測。

如果混在一起，配額恢復會被冷卻時間擋住，任務平白多等一分鐘。

---

## 3. 怎麼認出「這是配額問題」

`record_failure()` 會檢查錯誤訊息：

```python
markers = (
    "429",
    "rate limit",
    "rate_limit",
    "quota",
    "insufficient_quota",
    "insufficient quota",
    "billing hard limit",
    "exceeded your current quota",
    "resource_exhausted",
    "too many requests",
)
```

命中任一項 → `EXHAUSTED`（不熔斷，配額回來就好）
沒命中 → 走正常熔斷計數

---

## 4. 三個設計取捨

### 4.1 快取狀態，絕不在 `select()` 裡做 I/O

`select()` 在請求路徑上，必須快，也不能卡住 event loop。
所以它只**讀**記憶體裡的狀態，狀態由這三種方式更新：

| 什麼時候 | 呼叫什麼 |
|---|---|
| server 啟動 | `refresh_all()` |
| 背景排程（每 60 秒） | `refresh_all()` |
| 請求失敗／成功 | `record_failure()` / `record_success()` |
| `khavis health --refresh` | `refresh_all()` |

`select()` 自己永遠不發網路請求。

### 4.2 冷啟動時樂觀

沒探測過的 pool 是 `UNKNOWN`，**視為可用**。
否則新啟動的程序會有零候選、直接拒絕路由任何東西。

### 4.3 全部不可用時不硬送

```yaml
on_all_unavailable: "fail"     # select() 回傳 None，呼叫端丟清楚的 503
# on_all_unavailable: "degrade"  # 送到最近失敗最少的 pool（服務不中斷）
```

預設是 `fail`。理由：如果 12 個 pool 全部熔斷，送進哪一個都是錯的 ——
不如明確回報「全都掛了，原因是 X、Y、Z」，讓呼叫端（agent）決定要不要等。

要拿診斷訊息：

```python
router.select("程式碼")  # -> None
router.explain_unavailable("程式碼")
# -> "GLM-5.3: quota exhausted (remaining=0); NVIDIA-Cloud: circuit open
#     after 3 failures (12s / 60s cooldown), last error: ConnectTimeout"
```

---

## 5. 端到端 failover 流程

```
POST /v1/chat/completions  { "model": "...", "messages": [...] }
        │
        ▼
  _pick_plugin()  ──有 model → 鎖定該 pool（capability=None）
        │            沒 model → 推斷 capability（"程式碼" / "推理" / "中文" ...）
        ▼
  _call_with_failover()
        │
        ├─ 有 capability → select(cap, strategy="priority")  →  第一個健康的 pool
        │        │
        │        ├─ 成功 → record_success() → 回應
        │        │
        │        └─ 失敗 → record_failure()
        │                  │
        │                  ├─ 錯誤像配額 → EXHAUSTED（不熔斷）
        │                  └─ 否則 → 計數，可能熔斷
        │
        ├─ 重試（最多 4 次）
        │        │
        │        ├─ 有 capability → select(cap, ..., exclude={已試過的})
        │        │      維持同一類型，換 model 但不換能力
        │        │
        │        └─ 鎖定 model（capability=None）→ select_any(..., exclude={已試過的})
        │              任何其他健康 pool。鎖定的 pool 是唯一保證能提供「那個」
        │              model 的，但把 429 原樣丟回給 caller 正是這層要避免的，
        │              所以代價是回應可能來自不同的 model。
        │
        └─ 全部用完 → 502，或 503 + explain_unavailable()

背景每 60 秒：refresh_all() → 配額回來 / 冷卻到的 pool 自動復原
```

> **`model` 欄位是預設路徑。** 大部分 OpenAI 相容 client 每次請求都會帶 `model`，
> 這代表「鎖定 model」不是罕見邊緣情況，而是主要流量。修好之前這條路徑會在
> 第一次重試就跳出迴圈，等於 failover 從未啟用。

`strategy="priority"` 在這條路徑上是刻意的：它照 `capabilities.yaml` 的 `pools`
順序拿第一個可用的，行為可預測。隨機／加權適合分散負載，不適合 failover ——
failover 要的是「確定性」。

預設策略仍然是 `weighted`（為了不破壞既有呼叫端），要確定性請顯式指定 `priority`。

關掉 failover：`KHAVIS_FAILOVER=0`（會嚴格鎖定單一 pool，也代表「寧可回 429
也不換 model」）。

---

## 6. 設定

`config/health.yaml`：

```yaml
health:
  failure_threshold: 3        # 連續失敗幾次熔斷
  cooldown_s: 60.0            # 冷卻幾秒
  success_threshold: 1        # 半開狀態要成功幾次才關閉
  respect_quota: true         # 認真對待 check_quota()==0
  filter_router: true         # 在 select() 裡套用過濾
  on_all_unavailable: "fail"
  allow_half_open: true
```

環境變數覆蓋：

| 變數 | 對應欄位 |
|---|---|
| `KHAVIS_HEALTH_FAILURE_THRESHOLD` | `failure_threshold` |
| `KHAVIS_HEALTH_COOLDOWN_S` | `cooldown_s` |
| `KHAVIS_HEALTH_SUCCESS_THRESHOLD` | `success_threshold` |
| `KHAVIS_HEALTH_RESPECT_QUOTA` | `respect_quota` |
| `KHAVIS_HEALTH_FILTER_ROUTER` | `filter_router` |
| `KHAVIS_HEALTH_REFRESH_S` | 背景探測間隔（預設 60） |

---

## 7. CLI

```bash
# 只看目前快取的狀態（不發網路請求）
khavis health

# 先探測所有 pool 再印
khavis health --refresh

# 全部強制復原（手動解鎖熔斷的 pool）
khavis health --reset
```

輸出範例：

```
POOL              STATE      QUOTA  LAST ERROR
----------------------------------------------------------------
MiniMax-M3        closed      820/1000
GLM-5.3           exhausted     0/1000  quota exhausted
Ollama-Surface    open                circuit open after 3 failures (12s / 60s cooldown)
Ollama-Mac        closed     n/a

[health] 2 pool(s) not receiving traffic: GLM-5.3, Ollama-Surface
[health] fix with: khavis health --reset
```

---

## 8. Per-pool 權重

`capabilities.yaml` 現在支援兩種寫法：

```yaml
# 舊寫法：一個 weight 套用到清單裡所有 pool
- capability: "英文"
  pools: ["Google-Gemini", "OpenAI-API", "Ollama-Mac"]
  weight: 1.0

# 新寫法：每個 pool 各自的權重
- capability: "英文"
  pools:
    Google-Gemini: 1.2
    OpenAI-API:    0.85    # BYOK，額外要付錢 → 降權
    Ollama-Mac:    1.0     # 本機免費
```

舊寫法完全相容。這讓 `config/capabilities.yaml` 註解裡宣稱的
「BYOK pool 降權到 0.85」終於真的可以表達 —— 舊 schema 做不到這件事。
