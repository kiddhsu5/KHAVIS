# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.5.x   | ✅ |

## Reporting a vulnerability

**請勿在公開 issue 張貼漏洞細節、token 或任何秘密。**

請改用 GitHub 的私密回報管道：

1. 到本倉庫的 **Security → Advisories → Report a vulnerability**，建立一份 private advisory；
2. 描述影響範圍、重現步驟與（若有）建議修法；概念驗證請去除任何真實憑證；
3. 維護者會在 **7 個工作天內**回覆初步評估。

若你不方便使用 advisories，也可以開一則**不含技術細節**的 issue，只寫「有一個安全問題想私下回報」，維護者會與你聯繫私下管道。

## 我們如何處理

- **確認與分級**：先重現，再依影響（機密性 / 完整性 / 可用性）分級。
- **修復**：確認後優先處理高危與中危；修復前不公開技術細節。
- **揭露**：修復發佈後，於 release notes 說明影響面與升級建議；回報者將列名於致謝（可選擇匿名）。

## 範圍提醒

- 本專案整合第三方供應商與外部 CLI 工具；**這些第三方服務本身的漏洞不在本專案範圍**，請直接向對應供應商回報。
- `.env`、API keys、adapter token、Telegram token 屬於使用者本機秘密，請勿放進 issue、PR 或 CI log。
- 部署到公開網路前，請參考 `deploy/README.md` 的綁定與反代建議；未設存取控制的對外暴露屬於部署設定問題，但仍歡迎回報我們文件不清楚之處。
