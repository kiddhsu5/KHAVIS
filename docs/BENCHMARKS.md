# khavis benchmarks

> 自我披露的 router 開銷。Bifrost 公開 20μs / 5k req/s — 我們給出對等的數字，
> 而且寫成 CI regression test。

最後更新：2026-09-24
Python：3.12.14
主機：Apple Silicon

## 怎麼跑

```bash
# 從專案 root
python scripts/run_benchmarks.py
# 或：
pytest tests/benchmarks/ -v -s
```

CI 會跑 `pytest tests/benchmarks/` 並對預算（見下表）做 assertion。
預算是**硬上限**：任何一條 p95 達到預算就 fail。

## 數字（real registry、mock I/O）

| 量測項目 | 預算 (p95) | 實測 (p95) | 說明 |
| --- | --- | --- | --- |
| `discovery_ms` | 200 ms | **0.4 ms** | 12 個 plugin 的 importlib + class instantiate + YAML scan。冷啟動成本。 |
| `capability_load_ms` | 50 ms | **9.2 ms** | 解析 `config/capabilities.yaml` 並建 entry 結構。Hot reload 也走這條。 |
| `selection_ms`（每個 capability） | 2 ms | **0.02 ms** | router 熱路徑。每次請求都會跑。 |
| `chat_roundtrip_ms`（mock I/O） | 10 ms | **0.2 ms** | select + 呼叫 mock chat() 的 end-to-end router 開銷。 |

> 註：`selection_ms` 的預算給 2 ms 是為了留 noise headroom；實測 ~0.02 ms
> 表示 router 本身遠比網路 I/O 便宜 — 真正的瓶頸是上游 LLM API。

## 怎麼讀這些數字

### 跟對手對照

| 對手 | 公開數字 | 註 |
| --- | --- | --- |
| Bifrost | 20μs / 5k req/s | 公開的 marketing 數字 |
| K.H.A.V.I.S.（本檔） | selection p95 = 20μs（0.02 ms）；cold start = 0.4 ms | CI 強制 regression |

`selection_ms` 直接對標 Bifrost 的 20μs — 我們在同一量級。

### 怎麼看 cold start

`discovery_ms` 是「第一次 import + instantiate 12 個 plugin」的時間。
對一個常駐 daemon 來說，這只付一次。對 CLI 工具來說，每次啟動都付 —
目前 0.4 ms 不構成問題；如果未來想縮短，可以 lazy-import plugin。

### 怎麼看 roundtrip

`chat_roundtrip_ms` 的 0.2 ms 是「select 一個 plugin + 呼叫它的 mock chat」
的總時間，**不含**實際 HTTP request。
真實 production 流量會再加上上游 LLM API 的 latency（typical 200ms–2s），
router 開銷只占總時間的 0.01–0.1%。

## 預算怎麼定的

| 量測項目 | 預算 | 為什麼 |
| --- | --- | --- |
| `discovery_ms` | 200 ms | 12 個 plugin import；給編譯 / FS cache 留餘裕 |
| `capability_load_ms` | 50 ms | 純 YAML parse；Python `yaml.safe_load` 通常 < 10 ms |
| `selection_ms` | 2 ms | router 是同步 hot path；給 GC pause 留 100× headroom |
| `chat_roundtrip_ms` | 10 ms | mocked I/O；真實流量主要瓶頸是上游 |

預算故意定得寬鬆（遠高於實測 baseline），讓 CI 抓**回歸**而非
微秒級抖動。如果未來發現 baseline 變了（換硬體、升 Python），請一起更新
本檔案 + `tests/benchmarks/harness.py`（單一 `BUDGETS` 來源）。

## 已知不在這個 benchmark 範圍的

- **真實 LLM I/O**：這是 router 上游的事，benchmark 應該在 LLM API 供應商
  那一端做。我們只量 router 自己。
- **Streaming**：每個 provider 的 streaming protocol 不同；目前 chat()
  回傳 dict，串流走另一條路徑（見 `providers/*.py` 各自實作）。
- **Concurrency**：本 benchmark 是單執行緒。多 thread / asyncio 並發的
  overhead 沒在這裡。未來 roadmap 加。
- **記憶體**：plugin import 12 個 SDK 後的 RSS 沒在這裡量。
  `top -l 1 -pid $(pgrep -f "khavis")` 是一個起點。

## 變更歷史

- 2026-09-24：首次建立。`discovery_ms` 0.4ms / `selection_ms` 0.02ms /
  `chat_roundtrip_ms` 0.2ms。預算選擇刻意寬鬆以容忍 noise。
