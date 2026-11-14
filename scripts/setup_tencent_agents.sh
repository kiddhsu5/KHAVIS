#!/usr/bin/env bash
# =============================================================================
# K.H.A.V.I.S. 騰訊雲 Agent 集群與統一工作台一鍵環境檢測與啟動腳本
# =============================================================================
# 檢測項目:
#   1. 五大 CLI Agent 安裝狀態 (claude code, codex, antigravity, minimax code, opencode)
#   2. 四大 LLM 付費方案金鑰配置 (MiniMax, GLM, GPT GO, Antigravity AI Pro)
#   3. K.H.A.V.I.S. 核心路由、統一 Web 工作台 (Unified Workbench) 啟動
# =============================================================================

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}======================================================================${NC}"
echo -e "${CYAN}🚀 K.H.A.V.I.S. 騰訊雲 Agent 集群與統一工作台初始化檢測${NC}"
echo -e "${CYAN}======================================================================${NC}"

# 1. 檢測環境變數檔案
if [ ! -f ".env" ] && [ -f "deploy/tencent-cloud.env.example" ]; then
    echo -e "${YELLOW}[!] 未發現 .env 檔案，正在從 deploy/tencent-cloud.env.example 建立範本...${NC}"
    cp deploy/tencent-cloud.env.example .env
    echo -e "${GREEN}[✓] 已建立 .env 檔案，請記得填寫各方案 API 金鑰。${NC}"
fi

# 2. 檢測 5 大 CLI Agent 安裝路徑
echo -e "\n${CYAN}--- [1/3] 檢測騰訊雲主機上的 5 大 CLI Agent ---${NC}"

check_cli() {
    local name="$1"
    local bin="$2"
    local plan="$3"
    if command -v "$bin" >/dev/null 2>&1; then
        local ver
        ver=$("$bin" --version 2>&1 | tail -n 1 || echo "installed")
        echo -e "  • ${GREEN}[✓] $name ($bin)${NC}: $ver (綁定: $plan)"
    else
        echo -e "  • ${YELLOW}[?] $name ($bin)${NC}: 未在 PATH 中發現 (系統將啟用適配器模擬模式)"
    fi
}

check_cli "Claude Code" "claude" "預設模型 gemini-4-argon (供應商模型目錄)"
check_cli "OpenAI Codex" "codex" "預設模型 kimi-k3.1-pro (JUAPI TokenRouter)"
check_cli "Antigravity" "antigravity" "Google Antigravity Agentic CLI (agy / antigravity)"
check_cli "MiniMax Code" "minimax" "預設模型 MiniMax-M3.1-Flash (MiniMax Token Plan)"
check_cli "OpenCode" "opencode" "預設模型 GLM 5.3 FlashX (GLM Coding Plan)"

# 3. 檢測 4 大付費方案金鑰環境
echo -e "\n${CYAN}--- [2/3] 檢測四大 LLM 付費方案配置 ---${NC}"

check_key() {
    local plan_name="$1"
    local env_var="$2"
    if [ -n "${!env_var:-}" ]; then
        echo -e "  • ${GREEN}[✓] $plan_name ($env_var)${NC}: 已配置 (${#!env_var} 字元)"
    else
        echo -e "  • ${YELLOW}[!] $plan_name ($env_var)${NC}: 尚未設定 (可於 .env 中配置)"
    fi
}

check_key "MiniMax Token Plan" "MINIMAX_API_KEY"
check_key "GLM Coding Plan" "ZHIPUAI_API_KEY"
check_key "GPT GO (OpenAI)" "OPENAI_API_KEY"
check_key "Gemini AI Pro" "GOOGLE_API_KEY"

# 4. 啟動狀態與統一工作台伺服器
echo -e "\n${CYAN}--- [3/3] 統一工作台 (Unified Workbench) 連通說明 ---${NC}"
echo -e "  • 統一頁面工作台: ${GREEN}http://<騰訊雲主機IP>:8080/workbench${NC}"
echo -e "  • 即時監控儀表板: ${GREEN}http://<騰訊雲主機IP>:8080/dashboard${NC}"
echo -e "  • OpenAI 相容網關: ${GREEN}http://<騰訊雲主機IP>:8000/v1${NC}"
echo -e "\n${GREEN}檢測完成！您可以使用 'docker compose up -d' 或 'python3 -m scripts.status_server --port 8080' 啟動工作台。${NC}"
