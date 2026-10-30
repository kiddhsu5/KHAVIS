#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# K.H.A.V.I.S. 一鍵部署安裝器（Ubuntu 22.04+ / 騰訊雲 VM 驗證）
#
# 功能：
#   1. 檢查環境（Python ≥ 3.11、git）
#   2. Clone 專案到 $KHAVIS_HOME（預設 ~/khavis；已存在則 git pull 更新）
#   3. 建立 venv 並安裝依賴
#   4. 初始化 .env（已存在不覆蓋）
#   5. 安裝 systemd 服務：khavis-workbench（系統級，預設 port 8080）
#   6. --with-adapter：偵測到 hermes 時一併安裝 K.A.V.I.S. 派工橋接器
#      （user service kavis-adapter，OpenAI 相容 → hermes -z）
#   7. 啟動 + 健康檢查
#
# 用法：
#   bash deploy/install.sh                        # 標準安裝（~/khavis, port 8080）
#   bash deploy/install.sh --home /opt/khavis --port 8080 --with-adapter
#   bash deploy/install.sh --skip-clone           # 只更新依賴與服務（不動 repo）
#
# K.A.V.I.S. 內建 agent：
#   項目內建的唯一協同 agent「K.A.V.I.S.」= openclaw × hermes 執行環境
#   + LangGraph 7 角色團隊協同。--with-adapter 會把它接上本機 hermes CLI；
#   設定 KAVIS_TENCENT_DISPATCH_URL 後 Workbench 即可派工到該節點。
#
# Licensed under the Apache License, Version 2.0.
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

KHAVIS_HOME="${KHAVIS_HOME:-$HOME/khavis}"
KHAVIS_PORT="${KHAVIS_PORT:-8080}"
REPO="${REPO:-https://github.com/kiddhsu5/KHAVIS.git}"
BRANCH="${BRANCH:-main}"
RUN_USER="${RUN_USER:-$(id -un)}"
WITH_ADAPTER=false
SKIP_CLONE=false
NO_SERVICE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --home)          KHAVIS_HOME="$2"; shift 2 ;;
    --port)          KHAVIS_PORT="$2"; shift 2 ;;
    --repo)          REPO="$2"; shift 2 ;;
    --branch)        BRANCH="$2"; shift 2 ;;
    --user)          RUN_USER="$2"; shift 2 ;;
    --with-adapter)  WITH_ADAPTER=true; shift ;;
    --skip-clone)    SKIP_CLONE=true; shift ;;
    --no-service)    NO_SERVICE=true; shift ;;
    -h|--help) grep '^#' "$0" | head -32; exit 0 ;;
    *) echo "未知參數: $1"; exit 1 ;;
  esac
done

log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*"; exit 1; }

# ── 1. 環境檢查 ────────────────────────────────────────────────────
log "檢查環境…"
command -v git >/dev/null || fail "需要 git（sudo apt install -y git）"
PY_BIN=""
for cand in python3.13 python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY_BIN="$cand"; break
  fi
done
[[ -n "$PY_BIN" ]] || fail "需要 Python ≥ 3.11（https://www.python.org/downloads/）"
PY_VER="$("$PY_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
log "Python $PY_VER ($PY_BIN) ✓  git ✓"

# ── 2. 取得專案 ────────────────────────────────────────────────────
if [[ "$SKIP_CLONE" == "true" ]]; then
  log "略過 clone（--skip-clone），使用既有 $KHAVIS_HOME"
  [[ -d "$KHAVIS_HOME" ]] || fail "--skip-clone 需要已存在的 $KHAVIS_HOME"
elif [[ -d "$KHAVIS_HOME/.git" ]]; then
  log "$KHAVIS_HOME 已是 git repo → git pull 更新"
  git -C "$KHAVIS_HOME" fetch origin "$BRANCH"
  git -C "$KHAVIS_HOME" reset --hard "origin/$BRANCH"
else
  if [[ -d "$KHAVIS_HOME" ]]; then
    STAMP="$(date +%Y%m%d-%H%M%S)"
    BACKUP="${KHAVIS_HOME}-preinstall-${STAMP}"
    warn "$KHAVIS_HOME 已存在且不是 git repo → 搬到 $BACKUP（資料不會遺失）"
    mv "$KHAVIS_HOME" "$BACKUP"
    log "稍後請從 $BACKUP 回復：.env、config/workbench_catalog.json、workspace/、logs/"
  fi
  log "Clone $REPO ($BRANCH) → $KHAVIS_HOME"
  git clone --depth 1 -b "$BRANCH" "$REPO" "$KHAVIS_HOME"
fi

# ── 3. venv + 依賴 ─────────────────────────────────────────────────
log "建立 venv 並安裝依賴…"
"$PY_BIN" -m venv "$KHAVIS_HOME/.venv"
"$KHAVIS_HOME/.venv/bin/pip" install --upgrade pip -q
"$KHAVIS_HOME/.venv/bin/pip" install -r "$KHAVIS_HOME/requirements.txt" -q
log "依賴安裝完成"

# ── 4. .env 初始化（不覆蓋既有值）──────────────────────────────────
if [[ ! -f "$KHAVIS_HOME/.env" ]]; then
  cp "$KHAVIS_HOME/.env.example" "$KHAVIS_HOME/.env"
  log "已從 .env.example 建立 .env — 請填入供應商 API keys"
else
  log ".env 已存在，保留不動"
fi

# ── 5. systemd 服務（系統級 khavis-workbench）──────────────────────
SERVICE_INSTALLED=false
if [[ "$NO_SERVICE" == "true" ]] || ! command -v systemctl >/dev/null 2>&1; then
  log "略過 systemd 服務（無 systemctl 或 --no-service）→ 之後手動啟動："
  log "  $KHAVIS_HOME/.venv/bin/python $KHAVIS_HOME/scripts/status_server.py --port $KHAVIS_PORT"
else
log "安裝 khavis-workbench.service（port $KHAVIS_PORT）"
sudo tee /etc/systemd/system/khavis-workbench.service >/dev/null <<EOF
[Unit]
Description=K.H.A.V.I.S. Unified Web Workbench & Status Server
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${KHAVIS_HOME}
Environment=PYTHONUNBUFFERED=1
ExecStart=${KHAVIS_HOME}/.venv/bin/python ${KHAVIS_HOME}/scripts/status_server.py --port ${KHAVIS_PORT}
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now khavis-workbench
SERVICE_INSTALLED=true
fi

# ── 6. K.A.V.I.S. 派工橋接器（選配）────────────────────────────────
if [[ "$WITH_ADAPTER" == "true" ]]; then
  HERMES_BIN="${KHAVIS_HERMES_BIN:-$HOME/.hermes/hermes-agent/.hermes/bin/hermes}"
  if [[ -x "$HERMES_BIN" ]]; then
    log "偵測到 hermes ($HERMES_BIN) → 安裝 kavis-adapter（K.A.V.I.S. 遠端派工通道）"
    cp "$KHAVIS_HOME/deploy/kavis_adapter.py" "$HOME/.hermes/kavis_adapter.py"
    mkdir -p "$HOME/.config/systemd/user"
    sed -e "s|__HERMES_HOME__|$HOME/.hermes|g" \
        -e "s|__HERMES_HOME__|$HOME/.hermes|g" \
        "$KHAVIS_HOME/deploy/kavis-adapter.service" > "$HOME/.config/systemd/user/kavis-adapter.service"
    if command -v systemctl >/dev/null 2>&1; then
      systemctl --user daemon-reload
      systemctl --user enable --now kavis-adapter
    else
      log "無 systemctl → 手動啟動 adapter：$PY_BIN $HOME/.hermes/kavis_adapter.py"
    fi
    # Workbench .env 接上派工通道（不覆蓋既有值）
    if ! grep -q '^KAVIS_TENCENT_DISPATCH_URL=' "$KHAVIS_HOME/.env"; then
      printf '\n# K.A.V.I.S. 遠端派工（本機 hermes 橋接器）\nKAVIS_TENCENT_DISPATCH_URL=http://127.0.0.1:18081/v1/chat/completions\n' >> "$KHAVIS_HOME/.env"
      log "已寫入 KAVIS_TENCENT_DISPATCH_URL"
    fi
  else
    warn "找不到 hermes CLI（$HERMES_BIN）→ 略過 kavis-adapter。K.A.V.I.S. 以本地 LangGraph 模式運行。"
  fi
fi

# ── 7. 健康檢查 ────────────────────────────────────────────────────
if [[ "$SERVICE_INSTALLED" == "true" ]]; then
  log "等待服務啟動…"
  sleep 3
  systemctl is-active --quiet khavis-workbench || fail "khavis-workbench 未啟動（journalctl -u khavis-workbench -n 20）"
fi
HEALTH="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${KHAVIS_PORT}/healthz" || true)"
if [[ "$HEALTH" != "200" && "$SERVICE_INSTALLED" != "true" ]]; then
  log "尚未有常駐服務 → 背景啟動做健康檢查"
  "$KHAVIS_HOME/.venv/bin/python" "$KHAVIS_HOME/scripts/status_server.py" --port "$KHAVIS_PORT" >/tmp/khavis-install-run.log 2>&1 &
  sleep 4
  HEALTH="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${KHAVIS_PORT}/healthz" || true)"
fi
[[ "$HEALTH" == "200" ]] || fail "健康檢查失敗（HTTP $HEALTH）"
log "健康檢查 ✓ http://127.0.0.1:${KHAVIS_PORT}/healthz"

cat <<EOF

═══════════════════════════════════════════════════════
 K.H.A.V.I.S. 安裝完成
═══════════════════════════════════════════════════════
 專案目錄   : $KHAVIS_HOME
 Workbench  : http://127.0.0.1:${KHAVIS_PORT}/workbench
 內建 agent : K.A.V.I.S.（openclaw × hermes 環境 + LangGraph 7 角色協同）
 服務管理   : sudo systemctl {status|restart} khavis-workbench
 升級方式   : git -C $KHAVIS_HOME pull && sudo systemctl restart khavis-workbench

 後續手動步驟：
 1. 編輯 $KHAVIS_HOME/.env 填入供應商 API keys
 2. （對外服務）以 nginx 反代 443 → 127.0.0.1:${KHAVIS_PORT}
 3. （遠端派工）--with-adapter 已裝則免；否則設 KAVIS_TENCENT_DISPATCH_URL
═══════════════════════════════════════════════════════
EOF
