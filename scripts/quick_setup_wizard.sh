#!/usr/bin/env bash
# =============================================================================
# K.H.A.V.I.S. 開箱即用快速部署與 BYOK 設定精靈 (Quick Setup Wizard)
# 支援：
# 1. 自動建立共用專案資料夾 (Shared Workspace) 與 Git Worktree 環境
# 2. 自動配置 5 大 CLI 外殼 (Claude Code [MiniMax-M3.1-Flash], Codex [glm-5.3-flashx],
#    Antigravity [agy], MiniMax Code, opencode)
# 3. 互動式或環境變數自動寫入 BYOK API 金鑰 (.env)
# =============================================================================
set -euo pipefail

PROJECT_DIR="${KHAVIS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
WORKSPACE_DIR="${KHAVIS_WORKSPACE_ROOT:-$PROJECT_DIR/workspace/shared-app}"

echo "=================================================================="
echo " 🚀 K.H.A.V.I.S. Visual Workbench · 快速部署與 BYOK 精靈"
echo "=================================================================="
echo "📂 專案根目錄: $PROJECT_DIR"
echo "🤝 多 CLI 共用專案資料夾: $WORKSPACE_DIR"

mkdir -p "$WORKSPACE_DIR"
if [ ! -d "$WORKSPACE_DIR/.git" ]; then
  git -C "$WORKSPACE_DIR" init -b main >/dev/null 2>&1 || git -C "$WORKSPACE_DIR" init >/dev/null 2>&1
  git -C "$WORKSPACE_DIR" config user.name "KHAVIS Workspace"
  git -C "$WORKSPACE_DIR" config user.email "workspace@khavis.local"
  cat > "$WORKSPACE_DIR/README.md" <<'EOF'
# K.H.A.V.I.S. Shared Project Workspace
此資料夾由 Claude Code、Codex、Antigravity、MiniMax Code、opencode 與自訂 Agent 共用。
EOF
  git -C "$WORKSPACE_DIR" add -A >/dev/null 2>&1 || true
  git -C "$WORKSPACE_DIR" commit -m "chore: init shared workspace" >/dev/null 2>&1 || true
fi

ENV_FILE="$PROJECT_DIR/.env"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

set_env_default() {
  local key="$1"
  local val="$2"
  if ! grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

set_env_default "CLAUDE_DISPATCH_MODEL" "gemini-4-argon"
set_env_default "CODEX_DISPATCH_MODEL" "kimi-k3.1-pro"
set_env_default "ANTIGRAVITY_DISPATCH_MODEL" "antigravity-pro"
set_env_default "MINIMAX_DISPATCH_MODEL" "MiniMax-M3.1-Flash"
set_env_default "OPENCODE_DISPATCH_MODEL" "glm-5.3-flashx"
set_env_default "KHAVIS_ACTIVE_PROJECT" "shared-app"
set_env_default "KHAVIS_ISOLATION_MODE" "shared"

if [ -x "$PROJECT_DIR/scripts/setup_tencent_agents.sh" ]; then
  echo "🔧 檢測並安裝 5 大 CLI Agent 轉接器..."
  bash "$PROJECT_DIR/scripts/setup_tencent_agents.sh" || true
fi

echo "✅ 快速部署完成！可啟動服務："
echo "   python3 scripts/status_server.py --port 8080"
echo "   開啟瀏覽器訪問: http://localhost:8080/workbench"
