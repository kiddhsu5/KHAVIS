#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# K.H.A.V.I.S. 零接觸一鍵部署與升級安裝器（Ubuntu 22.04+ / 騰訊雲 VM 驗證）
#
# 功能：
#   1. 檢查環境（Python ≥ 3.11、git、curl）
#   2. 取得或更新專案到 $KHAVIS_HOME（預設 ~/khavis）
#      - 若舊目錄非 git repo，自動備份並無縫遷移 .env、workbench_catalog.json、
#        custom_agents.json、workspace/、logs/
#   3. 建立 .venv 並安裝 Python 依賴
#   4. 初始化與自動修復 .env（絕不覆蓋既有有效金鑰；自動補齊本機 hermes/codex 可用金鑰）
#   5. 安裝/修復 CLI Agent 執行器（antigravity / minimax / opencode 指向 .venv）
#   6. 安裝 systemd 服務：khavis-workbench.service（含 EnvironmentFile 載入 .env）
#   7. 自動偵測 hermes 並安裝 kavis-adapter（含 loginctl enable-linger 常駐保活）
#   8. 執行完整健康檢查（systemd + /healthz + CLI Agents）
#
# 一鍵執行方式（在騰訊雲主機上）：
#   bash ~/khavis/deploy/install.sh --with-adapter
#   或遠端全新安裝：
#   curl -fsSL https://raw.githubusercontent.com/kiddhsu5/KHAVIS/main/deploy/install.sh | bash -s -- --with-adapter
#
# 參數：
#   --home <path>      安裝路徑（預設 ~/khavis）
#   --port <port>      Workbench 監聽埠（預設 8080）
#   --branch <name>    Git 分支（預設 main）
#   --with-adapter     強制啟用 kavis-adapter（預設偵測到 hermes 即自動啟用）
#   --no-adapter       略過 kavis-adapter 安裝
#   --skip-clone       不執行 git clone/pull，僅使用現有目錄重建環境與服務
#
# Licensed under the Apache License, Version 2.0.
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

KHAVIS_HOME="${KHAVIS_HOME:-$HOME/khavis}"
KHAVIS_PORT="${KHAVIS_PORT:-8080}"
REPO="${REPO:-https://github.com/kiddhsu5/KHAVIS.git}"
BRANCH="${BRANCH:-main}"
RUN_USER="${RUN_USER:-$(id -un)}"
RUN_HOME="$(eval echo "~${RUN_USER}")"
WITH_ADAPTER="auto"
SKIP_CLONE=false
BACKUP_DIR=""
NO_SERVICE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --home)          KHAVIS_HOME="$2"; shift 2 ;;
    --port)          KHAVIS_PORT="$2"; shift 2 ;;
    --repo)          REPO="$2"; shift 2 ;;
    --branch)        BRANCH="$2"; shift 2 ;;
    --user)          RUN_USER="$2"; RUN_HOME="$(eval echo "~${RUN_USER}")"; shift 2 ;;
    --with-adapter)  WITH_ADAPTER="true"; shift ;;
    --no-adapter)    WITH_ADAPTER="false"; shift ;;
    --skip-clone)    SKIP_CLONE=true; shift ;;
    --no-service)    NO_SERVICE=true; shift ;;
    -h|--help)       grep '^#' "$0" | head -35; exit 0 ;;
    *) echo "未知參數: $1"; exit 1 ;;
  esac
done

log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[  ok  ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[ warn ]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[ fail ]\033[0m %s\n' "$*"; exit 1; }

# ── 1. 環境檢查 ────────────────────────────────────────────────────
log "檢查系統環境…"
command -v git >/dev/null || fail "需要 git（sudo apt-get update && sudo apt-get install -y git）"
command -v curl >/dev/null || fail "需要 curl（sudo apt-get install -y curl）"
# 優先選 wheels 完整的版本（3.13/3.12/3.11）；3.14+ 部分套件需本機編譯
PY_BIN=""
for cand in python3.13 python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY_BIN="$cand"; break
  fi
done
[[ -n "$PY_BIN" ]] || fail "需要 Python ≥ 3.11（https://www.python.org/downloads/）"
PY_VER="$("$PY_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
ok "Python $PY_VER ($PY_BIN) · git · curl"

# ── 2. 取得或更新專案（含舊版非 git 目錄自動遷移）────────────────────
if [[ "$SKIP_CLONE" == "true" ]]; then
  log "略過 git 更新（--skip-clone），使用既有目錄 $KHAVIS_HOME"
  [[ -d "$KHAVIS_HOME" ]] || fail "--skip-clone 需要已存在的 $KHAVIS_HOME"
elif [[ -d "$KHAVIS_HOME/.git" ]]; then
  log "$KHAVIS_HOME 已是 git repo → 更新至 origin/$BRANCH"
  git -C "$KHAVIS_HOME" fetch origin "$BRANCH"
  if [[ -n "$(git -C "$KHAVIS_HOME" status --porcelain)" ]]; then
    STAMP="$(date +%Y%m%d-%H%M%S)"
    git -C "$KHAVIS_HOME" stash push -u -m "install-autostash-${STAMP}" >/dev/null || true
    warn "已將本地未提交變更暫存至 git stash (install-autostash-${STAMP})"
  fi
  git -C "$KHAVIS_HOME" checkout -B "$BRANCH" "origin/$BRANCH"
else
  if [[ -d "$KHAVIS_HOME" ]]; then
    STAMP="$(date +%Y%m%d-%H%M%S)"
    BACKUP_DIR="${KHAVIS_HOME}-preinstall-${STAMP}"
    warn "$KHAVIS_HOME 不是 git repo → 備份至 $BACKUP_DIR 並自動遷移設定與工作區"
    mv "$KHAVIS_HOME" "$BACKUP_DIR"
  fi
  log "Clone $REPO ($BRANCH) → $KHAVIS_HOME"
  git clone --depth 1 -b "$BRANCH" "$REPO" "$KHAVIS_HOME"

  if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
    [[ -f "$BACKUP_DIR/.env" ]] && cp -a "$BACKUP_DIR/.env" "$KHAVIS_HOME/.env"
    for cfg_file in workbench_catalog.json custom_agents.json; do
      if [[ -f "$BACKUP_DIR/config/$cfg_file" ]]; then
        cp -a "$BACKUP_DIR/config/$cfg_file" "$KHAVIS_HOME/config/$cfg_file"
      fi
    done
    if [[ -d "$BACKUP_DIR/workspace" ]]; then
      mkdir -p "$KHAVIS_HOME/workspace"
      cp -rn "$BACKUP_DIR/workspace/." "$KHAVIS_HOME/workspace/" 2>/dev/null || true
    fi
    if [[ -d "$BACKUP_DIR/logs" ]]; then
      mkdir -p "$KHAVIS_HOME/logs"
      cp -rn "$BACKUP_DIR/logs/." "$KHAVIS_HOME/logs/" 2>/dev/null || true
    fi
    ok "已從 $BACKUP_DIR 自動遷回 .env、config、workspace 與 logs"
  fi
fi

# ── 3. 建立 .venv 並安裝依賴 ────────────────────────────────────────
log "建立 Python 虛擬環境並安裝依賴…"
if [[ ! -x "$KHAVIS_HOME/.venv/bin/python" ]]; then
  "$PY_BIN" -m venv "$KHAVIS_HOME/.venv"
fi
"$KHAVIS_HOME/.venv/bin/pip" install --upgrade pip -q
"$KHAVIS_HOME/.venv/bin/pip" install -r "$KHAVIS_HOME/requirements.txt" -q
chmod +x "$KHAVIS_HOME/deploy/cli_shim.py" "$KHAVIS_HOME/deploy/kavis_adapter.py" 2>/dev/null || true
ok "Python .venv 依賴就緒"

# ── 4. 初始化與修復 .env（不覆蓋有效金鑰，安全權限 600）─────────────
if [[ ! -f "$KHAVIS_HOME/.env" ]]; then
  cp "$KHAVIS_HOME/.env.example" "$KHAVIS_HOME/.env"
  log "已從 .env.example 建立 $KHAVIS_HOME/.env"
fi
chmod 600 "$KHAVIS_HOME/.env"

# 若本機已有 hermes / codex 有效憑證且 .env 內為空或佔位符，安全同步至 .env（絕不印出金鑰內容）
"$KHAVIS_HOME/.venv/bin/python" - "$KHAVIS_HOME/.env" "$RUN_HOME" <<'PY'
import json
import sys
from pathlib import Path
from dotenv import dotenv_values, set_key

env_path = Path(sys.argv[1])
run_home = Path(sys.argv[2])
cur = dotenv_values(env_path)

def is_placeholder(val: str | None) -> bool:
    s = (val or "").strip()
    if not s:
        return True
    low = s.lower()
    return low.startswith(("your_", "missing_", "<")) or low in {"placeholder", "changeme", "none"}

# 1. Sync MiniMax CN key from ~/.hermes/.env if MINIMAX_API_KEY is missing or fails auth (401/1004)
def minimax_key_works(k: str) -> bool:
    if not k or is_placeholder(k):
        return False
    import urllib.request, urllib.error
    for host in ("https://api.minimax.cn/v1/models", "https://api.minimaxi.com/v1/models"):
        req = urllib.request.Request(host, headers={"Authorization": f"Bearer {k}"})
        try:
            with urllib.request.urlopen(req, timeout=4) as r:
                if r.status == 200:
                    return True
        except urllib.error.HTTPError as e:
            if e.code != 401:
                # 404 on /v1/models means host reachable; test tiny completion
                pass
        except Exception:
            pass
    for host in ("https://api.minimax.cn/v1/chat/completions", "https://api.minimaxi.com/v1/chat/completions"):
        req = urllib.request.Request(
            host,
            data=json.dumps({"model": "MiniMax-M3.1-Flash", "messages": [{"role": "user", "content": "ok"}], "max_tokens": 4}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {k}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
    return False

hermes_env_path = run_home / ".hermes" / ".env"
if hermes_env_path.is_file():
    henv = dotenv_values(hermes_env_path)
    mm_cn = (henv.get("MINIMAX_CN_API_KEY") or "").strip()
    cur_mm = (cur.get("MINIMAX_API_KEY") or "").strip()
    if mm_cn and mm_cn != cur_mm and not minimax_key_works(cur_mm) and minimax_key_works(mm_cn):
        set_key(str(env_path), "MINIMAX_API_KEY", mm_cn, quote_mode="never")
        set_key(str(env_path), "MINIMAX_CN_API_KEY", mm_cn, quote_mode="never")
        set_key(str(env_path), "ANTHROPIC_BASE_URL", "https://api.minimax.cn/anthropic", quote_mode="never")
        print("[install] 已從 ~/.hermes/.env 自動修復並同步有效 MiniMax Token Plan 金鑰")

# 2. Sync JUAPI token from ~/.codex/auth.json if JUAPI_API_KEY is missing or truncated placeholder
codex_auth = run_home / ".codex" / "auth.json"
if codex_auth.is_file():
    try:
        cdata = json.loads(codex_auth.read_text(encoding="utf-8"))
        ckey = str(cdata.get("OPENAI_API_KEY") or "").strip()
        cur_ju = (cur.get("JUAPI_API_KEY") or "").strip()
        if len(ckey) >= 30 and (is_placeholder(cur_ju) or len(cur_ju) < 20):
            set_key(str(env_path), "JUAPI_API_KEY", ckey, quote_mode="never")
            print("[install] 已從 ~/.codex/auth.json 同步有效 JUAPI TokenRouter 金鑰")
    except Exception:
        pass
PY
chmod 600 "$KHAVIS_HOME/.env"

# ── 5. 安裝/修復 CLI Agent 執行器（antigravity / minimax / opencode）───
log "配置 CLI Agent 執行器（使用 $KHAVIS_HOME/.venv/bin/python）…"
install_cli_shim() {
  local bin_path="$1"
  local name
  name="$(basename "$bin_path")"
  # 若已是原生二進位檔（非 Python 舊版腳本）則保留不動
  if [[ -f "$bin_path" && ! -L "$bin_path" ]]; then
    if ! head -n 5 "$bin_path" 2>/dev/null | grep -qi "python"; then
      ok "保留既有原生 CLI: $bin_path"
      return 0
    fi
  fi
  sudo tee "$bin_path" >/dev/null <<EOF
#!${KHAVIS_HOME}/.venv/bin/python
import os, runpy
os.environ.setdefault("KHAVIS_HOME", "${KHAVIS_HOME}")
os.environ["KHAVIS_SHIM_NAME"] = "${name}"
runpy.run_path("${KHAVIS_HOME}/deploy/cli_shim.py", run_name="__main__")
EOF
  sudo chmod 755 "$bin_path"
  ok "已配置 CLI Agent 執行器: $bin_path ($name)"
}

install_cli_shim "/usr/local/bin/antigravity"
install_cli_shim "/usr/local/bin/minimax"
install_cli_shim "/usr/local/bin/opencode"
if [[ -f "/usr/bin/mcode" ]] && head -n 5 "/usr/bin/mcode" 2>/dev/null | grep -qi "python"; then
  install_cli_shim "/usr/bin/mcode"
fi

# ── 6. K.A.V.I.S. 派工橋接器（自動偵測 hermes）──────────────────────
HERMES_BIN="${KHAVIS_HERMES_BIN:-$RUN_HOME/.hermes/hermes-agent/.hermes/bin/hermes}"
if [[ "$WITH_ADAPTER" == "auto" ]]; then
  [[ -x "$HERMES_BIN" ]] && WITH_ADAPTER="true" || WITH_ADAPTER="false"
fi

if [[ "$WITH_ADAPTER" == "true" ]]; then
  if [[ -x "$HERMES_BIN" ]]; then
    log "偵測到 hermes ($HERMES_BIN) → 部署並啟動 kavis-adapter.service"
    mkdir -p "$RUN_HOME/.hermes" "$RUN_HOME/.config/systemd/user"
    cp "$KHAVIS_HOME/deploy/kavis_adapter.py" "$RUN_HOME/.hermes/kavis_adapter.py"
    chmod 755 "$RUN_HOME/.hermes/kavis_adapter.py"
    sed -e "s|__HERMES_HOME__|$RUN_HOME/.hermes|g" \
        -e "s|__HOME__|$RUN_HOME|g" \
        "$KHAVIS_HOME/deploy/kavis-adapter.service" > "$RUN_HOME/.config/systemd/user/kavis-adapter.service"
    sudo loginctl enable-linger "$RUN_USER" 2>/dev/null || true
    systemctl --user daemon-reload
    systemctl --user enable kavis-adapter >/dev/null 2>&1 || true
    systemctl --user restart kavis-adapter
    if ! grep -q '^KAVIS_TENCENT_DISPATCH_URL=' "$KHAVIS_HOME/.env"; then
      printf '\n# K.A.V.I.S. 遠端派工（本機 hermes 橋接器）\nKAVIS_TENCENT_DISPATCH_URL=http://127.0.0.1:18081/v1/chat/completions\n' >> "$KHAVIS_HOME/.env"
    fi
    ok "kavis-adapter 已啟動 (127.0.0.1:18081)"
  else
    warn "未偵測到 hermes CLI ($HERMES_BIN) → K.A.V.I.S. 將以內建多池模式運行"
  fi
fi

# ── 7. 安裝並重啟 systemd 服務（khavis-workbench）──────────────────
SERVICE_INSTALLED=false
if [[ "$NO_SERVICE" == "true" ]] || ! command -v systemctl >/dev/null 2>&1; then
  log "略過 systemd 服務（無 systemctl 或 --no-service）→ 之後手動啟動："
  log "  $KHAVIS_HOME/.venv/bin/python $KHAVIS_HOME/scripts/status_server.py --port $KHAVIS_PORT"
else
log "安裝並啟動 khavis-workbench.service（port $KHAVIS_PORT）…"
sudo tee /etc/systemd/system/khavis-workbench.service >/dev/null <<EOF
[Unit]
Description=K.H.A.V.I.S. Unified Web Workbench & Status Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${KHAVIS_HOME}
Environment=PYTHONUNBUFFERED=1
Environment=KHAVIS_HOME=${KHAVIS_HOME}
Environment=PATH=${RUN_HOME}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=-${KHAVIS_HOME}/.env
ExecStart=${KHAVIS_HOME}/.venv/bin/python ${KHAVIS_HOME}/scripts/status_server.py --port ${KHAVIS_PORT}
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable khavis-workbench >/dev/null 2>&1 || true
sudo systemctl restart khavis-workbench
SERVICE_INSTALLED=true
fi

# ── 8. 健康檢查與驗證 ──────────────────────────────────────────────
if [[ "$SERVICE_INSTALLED" != "true" ]]; then
  log "背景啟動做健康檢查（之後請手動啟動）"
  "$KHAVIS_HOME/.venv/bin/python" "$KHAVIS_HOME/scripts/status_server.py" --port "$KHAVIS_PORT" >/tmp/khavis-install-run.log 2>&1 &
  sleep 4
fi
log "等待服務就緒並執行健康檢查…"
HEALTH="000"
for _ in 1 2 3 4 5 6; do
  sleep 1
  if systemctl is-active --quiet khavis-workbench; then
    HEALTH="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${KHAVIS_PORT}/healthz" || echo "000")"
    [[ "$HEALTH" == "200" ]] && break
  fi
done

systemctl is-active --quiet khavis-workbench \
  || fail "khavis-workbench 未啟動，請檢查：sudo journalctl -u khavis-workbench -n 40"
[[ "$HEALTH" == "200" ]] \
  || fail "健康檢查失敗（HTTP $HEALTH），請檢查：curl -v http://127.0.0.1:${KHAVIS_PORT}/healthz"
ok "khavis-workbench 運行正常 (HTTP 200 @ 127.0.0.1:${KHAVIS_PORT}/healthz)"

for cli_bin in claude codex antigravity minimax opencode; do
  if command -v "$cli_bin" >/dev/null 2>&1; then
    ver="$("$cli_bin" --version 2>&1 | tail -n 1 || echo "installed")"
    ok "CLI Agent [$cli_bin]: $ver"
  else
    warn "CLI Agent [$cli_bin]: 未安裝（將自動走內建 HTTP Failover）"
  fi
done

if [[ "$WITH_ADAPTER" == "true" && -x "$HERMES_BIN" ]]; then
  ADAPTER_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 "http://127.0.0.1:18081/healthz" || echo "000")"
  [[ "$ADAPTER_CODE" == "200" ]] && ok "kavis-adapter 健康檢查通過 (HTTP 200)" || warn "kavis-adapter 回傳 HTTP $ADAPTER_CODE"
fi

cat <<EOF

═══════════════════════════════════════════════════════════════════
 🚀 K.H.A.V.I.S. 部署與自我檢測完成！
═══════════════════════════════════════════════════════════════════
 專案目錄   : $KHAVIS_HOME
 本機工作台 : http://127.0.0.1:${KHAVIS_PORT}/workbench
 服務狀態   : sudo systemctl status khavis-workbench
 即時日誌   : sudo journalctl -u khavis-workbench -f
 一鍵更新   : bash $KHAVIS_HOME/deploy/install.sh --with-adapter
═══════════════════════════════════════════════════════════════════
EOF
