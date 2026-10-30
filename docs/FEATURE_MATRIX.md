# K.H.A.V.I.S. 功能對照表

> 誠實對外公開「做了什麼、沒做什麼」。對手 LiteLLM / Bifrost 都靠這種
> 「對照透明」累積信任 — 純粹主義定位反而讓透明變成競爭優勢。

最後更新：2026-09-24

## 1. 定位對照

| 維度 | K.H.A.V.I.S. | LiteLLM | Bifrost | Portkey | OpenRouter | AutoGen |
| --- | --- | --- | --- | --- | --- | --- |
| License | **Apache 2.0** | MIT（後改 Elite） | Apache 2.0 | Apache 2.0 + 商業版 | Proprietary | MIT + 商業 |
| 自架 | ✅ | ✅ | ✅ | ✅ | ❌（託管） | ✅（靠 Azure） |
| 零 markup | ✅ | ❌ | ❌ | ❌ | ✅（託管費） | ❌ |
| 付費 tier | **無** | Talk-to-Sales | Custom | $49/月起 | 用量計價 | 走 Azure 變現 |
| BYOK | ✅ | ✅ | ✅ | ✅（也提供代收費） | ❌（必須透過他們） | ✅ |

> 結論：K.H.A.V.I.S. 是「五家之中唯一同時 Apache 2.0 + 零付費 tier + 完全 BYOK」。
> 這個組合在 2026 年的開源 LLM 工具鏈裡沒有第二家。

## 2. 核心路由功能

| 功能 | K.H.A.V.I.S. | LiteLLM | Bifrost | Portkey | OpenRouter |
| --- | --- | --- | --- | --- | --- |
| 統一 chat() 介面 | ✅ | ✅ | ✅ | ✅ | ✅ |
| Capability-based 路由 | ✅（`code / vision / long-context / cheap / local` + 中文語意標籤） | ⚠️（`model-group`） | ⚠️（沿用 OpenAI `model name`） | ⚠️（provider-only） | ⚠️（provider-only） |
| Weighted random / round-robin | ✅ | ⚠️ | ✅ | ✅ | ❌ |
| Hot reload（YAML 改完即生效） | ✅（`watchdog`） | ⚠️（需重啟） | ✅ | ✅ | n/a |
| YAML-first 設定 | ✅ | ⚠️（env + YAML） | ✅ | ⚠️ | ❌ |
| 多地域 / 跨機 failover | ✅（`Ollama-Mac` + `Ollama-Surface` LAN 範例） | ❌ | ⚠️（需 Enterprise） | ✅ | ✅ |

## 3. Provider / 池子覆蓋

| Provider | K.H.A.V.I.S. 池 | LiteLLM | Bifrost | Portkey |
| --- | --- | --- | --- | --- |
| MiniMax-M3 / GLM-5.3（中文系） | ✅ | ✅ | ⚠️ | ⚠️ |
| Google Gemini Flash / Pro | ✅ | ✅ | ✅ | ✅ |
| NVIDIA Cloud | ✅ | ✅ | ✅ | ✅ |
| ByteDance Volcano Ark（×3） | ✅ | ✅ | ⚠️ | ⚠️ |
| OpenRouter（當上游） | ✅ | ✅ | ✅ | n/a |
| OpenAI Platform API（BYOK） | ✅ | ✅ | ✅ | ✅ |
| Anthropic Claude API（BYOK） | ✅ | ✅ | ✅ | ✅ |
| Ollama（local） | ✅ | ✅ | ✅ | ⚠️ |
| Ollama-Mac + Ollama-Surface（**雙機 LAN**） | ✅ | ❌ | ❌ | ❌ |

> Ollama 雙機模式是 K.H.A.V.I.S. 的獨家功能。其他 4 家都需要把 local model
> 跑在 gateway 同機；K.H.A.V.I.S. 把「我的 Mac 跑大模型 + Surface 跑小模型」
> 變成 first-class 設定。

## 4. 多代理 / Agent

| 功能 | K.H.A.V.I.S. | LiteLLM | AutoGen | LangGraph |
| --- | --- | --- | --- | --- |
| Planner / Coder / Critic pipeline | ✅（`agents/`） | ❌ | ✅ | ✅ |
| 三層記憶（working / episodic / semantic） | ✅ | ❌ | ⚠️ | ⚠️ |
| 角色辯論 / 驗證節點 | ✅（`agents/nodes/`） | ❌ | ✅ | ⚠️ |
| 與 AutoGen / LangGraph 互通 | **目標** | n/a | ✅ | ✅ |
| 自我聲明 | 「thin orchestrator」 | 「不代理」 | 「framework」 | 「framework」 |

> 刻意不做：完整 framework。`agents/` 模組定位是「為 router 服務的 orchestrator」，
> 不是取代 AutoGen / LangGraph。這條線畫清楚才不會失焦。

## 5. 派工 / Bot

| 功能 | K.H.A.V.I.S. | LiteLLM | Bifrost | Portkey | OpenRouter |
| --- | --- | --- | --- | --- | --- |
| Telegram bot | ✅ | ❌ | ❌ | ❌ | ❌ |
| 多 backend 平行派工 + 聚合 | ✅（Claude Code / Codex / 自家 router） | ❌ | ❌ | ❌ | ❌ |
| Webhook + long polling 兩種模式 | ✅ | ❌ | ❌ | ❌ | ❌ |
| Web dashboard | ✅（`/dashboard`） | ❌ | ❌ | ⚠️（Enterprise） | ✅ |

> 4 家對手都沒有官方 Telegram bot。`bot/` 模組（見 `docs/TELEGRAM_BOT.md`）
> 是真護城河。

## 6. 觀測 / 維運

| 功能 | K.H.A.V.I.S. | LiteLLM | Bifrost | Portkey |
| --- | --- | --- | --- | --- |
| 每次呼叫的 cost / latency / capability audit log | ✅ | ✅ | ✅ | ✅ |
| `/healthz` 端點 | ✅ | ⚠️ | ✅ | ✅ |
| 公開 health badge | ✅（`/healthz` + `/dashboard`） | ⚠️ | ✅ | ✅ |
| Prometheus exporter | 🔜 | ⚠️（Enterprise） | ✅ | ✅ |
| token-aware 成本預算（per request / session） | ✅（`core/budget.py`） | ❌ | ⚠️ | ⚠️ |
| 自我 benchmark（latency / p95） | ✅（`tests/benchmarks/`） | ⚠️ | ✅（公開 20μs / 5k req/s） | ⚠️ |

> K.H.A.V.I.S. 的 benchmark 數字自家已能跑（見 `tests/benchmarks/`），
> 詳見 `docs/BENCHMARKS.md`（如已生成）。

## 7. 明確「沒做」

> 與其裝作什麼都會做，這裡直接列出 K.H.A.V.I.S. **不做**的功能，避免用戶期待錯配。

| 不做的功能 | 替代方案 |
| --- | --- |
| Semantic cache | 使用者自行加 Redis（在 plugin 層做即可） |
| Prompt management / versioning | 不做。個人 / homelab 場景不需要 |
| Guardrails（成本 cap / PII 過濾 / rate limit） | 由 plugin 介面開放，社群維護；保留為企業入場券 |
| SSO / SAML / RBAC | 不做。與「個人 / homelab」定位衝突 |
| 用量計價 / 月費 | 不做。Apache 2.0 永久免費 |
| 託管 gateway | 不做。商業模式拒絕 SaaS |
| AutoGen 等級的完整 agent framework | 不做。互通優先（見 §4） |
| Web UI | ✅ 初版（pool health + dispatch history + wall） |

## 8. 怎麼讀這份表

- ✅ = 已實作（程式碼可查）
- ⚠️ = 部分支援或有條件限制
- 🔜 = 在 roadmap 上、尚未完成
- ❌ = 對手不支援或 K.H.A.V.I.S. 明確不做

對任何 ✅ 或 ⚠️ 的 cell 想知道「怎麼用」，請翻 `docs/FAQ.md` 與 `docs/OPERATIONS.md`。
對 🔜 想催進度，開 GitHub Issue 標 `strategy-roadmap` 即可。

## 9. 數據來源

- K.H.A.V.I.S. 欄：直接從本 repo 程式碼 / `config/*.yaml` / `tests/` 整理（截至 2026-09-24）
- LiteLLM：Bifrost 行業標準對照表（`BUSINESS_PLAN.md` 附錄 B），LiteLLM license 改 Elite 為公開紀錄
- Bifrost / Portkey：對手官網 Enterprise 表（2026-Q3）
- OpenRouter：託管服務，無自架選項
- AutoGen：依賴 Azure 變現為公開商業模式
