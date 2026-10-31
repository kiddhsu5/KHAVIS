# 功能宣稱與證據等級（Evidence Matrix）

本檔案是 KHAVIS 對外宣稱的**自我约束**：任何公開文件、簡報或貼文提到的功能，都必須對得上這裡標示的證據等級。等級不到的，只能寫成「規劃中」或「整合目標」，不得寫成已驗證能力。

## 等級定義

| 等級 | 意義 | 允許的對外說法 |
| --- | --- | --- |
| **E0 文件自述** | 只有設計與文件，沒有可重現證據 | 「設計上」「整合目標」「規劃中」 |
| **E1 內部測試** | 單元測試／契約測試在 repo 內可重複通過 | 「有測試覆蓋」 |
| **E2 可重現** | 在乾淨環境照文件指令即可重現（可含明確的前置條件） | 「照文件可重現」 |
| **E3 外部重現** | 第三方在自己的環境重現成功 | 「已被外部使用者驗證」 |

目前**沒有任何功能宣稱達到 E3**。歡迎以 issue 回報你的重現結果，讓對應項目升級。

## 矩陣（v0.5.0）

| 功能宣稱 | 等級 | 證據／重現方式 | 限制 |
| --- | --- | --- | --- |
| Provider registry 與 plugin 自動發現 | E1 | `tests/` 單元測試 | pool 是否可用仍依設定 |
| Capability-based selection | E1 | 單元測試（router、capability tags） | 需自行配置 capabilities.yaml |
| `khavis chat` / `khavis doctor` | E1 | 單元測試 | 真實請求需 BYOK，見下行 |
| 真實供應商請求（chat / doctor --deep） | E2（條件式） | 文件指令 + 自己的 provider key | 非離線可證；供應商可用性不在本專案保證範圍 |
| OpenAI 相容 HTTP API（`khavis serve`） | E1 | `tests/test_server_api.py` | 上游仍需配置 |
| 離線 failover 演練 `examples/zero_quota_demo.py` | **E2** | 無 key、無網路直接可跑，輸出含 `external_network: false` | 只驗證控制流程，不是可用性證明 |
| Workbench 對話串／歷史分組與搜尋 | E1 | `tests/test_workbench_c.py`（DOM 契約與 JS 語法） | 視覺差異未做逐像素驗證 |
| 每輪產出文檔內聯清單（下載／預覽） | E1 | 單元測試（output files、白名單） | 預覽型別有上限與白名單 |
| 執行明細全頁（含交叉審查） | E1 | 單元測試 | 審查內容取決於 agent 是否回傳 |
| 資料庫依對話串歸檔 | E1 + E2 | 單元測試 + 部署端 API 實測（thread 分組正確） | 歷史紀錄缺漏的檔案落入未歸檔 |
| 模型「抓取 → 勾選」與刪除持久化 | E1 + E2 | 單元測試 + 部署端實測（刪除後 capabilities 不復活） | 供應商 `/models` 不可達時顯示原因 |
| K.A.V.I.S. LangGraph 7 角色團隊 | E1（圖譜測試）／E2（條件式） | `tests/` + `examples/relay_quickstart/run_byok.py`（需 BYOK） | 真實執行需 provider key 與 agents extra |
| kavis adapter（OpenAI 相容 → hermes） | E2（於自有部署實測） | 端對端派工實測通過 | 外部重現需自行安裝 hermes；目前無 E3 |
| 外部 CLI 整合（Claude Code / Codex / 其他） | E0 | 僅整合介面與文件 | 須自行安裝、登入；相容性以各 CLI 為準 |
| Telegram bot | E0–E1 | 文件與部分單元測試 | 需自備 bot token |
| `deploy/install.sh`（Ubuntu） | E2 | 於 Ubuntu VM 實際執行完成（clone→venv→服務→健康檢查） | 其他發行版未逐一驗證 |
| `deploy/install.ps1`（Windows） | E0–E1 | 腳本提供與語法檢查 | **尚無 Windows 實機驗證**，歡迎回報 |
| Docker / nginx 範例 | E0 | 範例檔 | 部署環境差異需自行調整 |

## 升級規則

1. 任何項目要升 E2：必須附上「乾淨環境可照做的指令 + 預期輸出」。
2. 要升 E3：需要至少一位非維護者的重現回報（issue 連結）。
3. 找不到對應證據的新功能，先標 E0，不准先寫進「已支援」。
4. 數字（池數量、延遲、成功率）一律以可重現的實測為準；早期小樣本只用於學習，不做效率或市場推論。
