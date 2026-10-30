#!/usr/bin/env bash
# Setup helper for K.H.A.V.I.S.'s local Ollama pools (Mac + Surface).
#
# Validates that Ollama is reachable on each target host, pulls the
# required model if it is missing, smoke-tests a 1-token generation,
# and (Surface only) writes SURFACE_IP=<addr> into the .env file.
#
# Idempotent: safe to re-run — already-present models are skipped.
#
# Usage:
#   bash scripts/setup_ollama.sh                      # Mac + Surface (prompts for IP)
#   bash scripts/setup_ollama.sh --mac               # only the Mac pool
#   bash scripts/setup_ollama.sh --surface --ip 192.168.1.42
#   bash scripts/setup_ollama.sh --mac --surface --ip 192.168.1.42
#
# Exit codes:
#   0  all targets passed
#   1  one or more targets failed (details printed)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

# ---- defaults ---------------------------------------------------------
DO_MAC=0
DO_SURFACE=0
SURFACE_IP_ARG=""
PULL_ONLY=0

# Per-target model list — bump here when adding/changing the local default.
MAC_REQUIRED_MODELS=("gemma4:e2b")
SURFACE_REQUIRED_MODELS=("qwen2.5:1.5b")

# ---- arg parsing ------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mac) DO_MAC=1; shift ;;
    --surface) DO_SURFACE=1; shift ;;
    --ip) SURFACE_IP_ARG="${2:-}"; shift 2 ;;
    --pull-only) PULL_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

# If neither flag is set, do both.
if [[ $DO_MAC -eq 0 && $DO_SURFACE -eq 0 ]]; then
  DO_MAC=1
  DO_SURFACE=1
fi

# ---- helpers ----------------------------------------------------------
if [[ -t 1 ]]; then
  C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_WARN=$'\033[33m'; C_RST=$'\033[0m'
else
  C_OK=""; C_BAD=""; C_WARN=""; C_RST=""
fi

log()  { printf '%s\n' "$*"; }
ok()   { printf '  %s✓ %s%s\n' "$C_OK"   "$*" "$C_RST"; }
bad()  { printf '  %s✗ %s%s\n' "$C_BAD"  "$*" "$C_RST"; }
warn() { printf '  %s! %s%s\n' "$C_WARN" "$*" "$C_RST"; }
hdr()  { printf '\n%s== %s ==%s\n' "$C_OK" "$*" "$C_RST"; }

has_ollama_cli() {
  command -v ollama >/dev/null 2>&1
}

# probe_endpoint <base_url>  →  exit 0 if /api/tags responds
probe_endpoint() {
  local url="$1/api/tags"
  curl --silent --fail --max-time 5 -o /dev/null "$url"
}

list_remote_models() {
  local base="$1"
  curl --silent --fail --max-time 5 "$base/api/tags" \
    | grep -oE '"name"[[:space:]]*:[[:space:]]*"[^"]+"' \
    | sed -E 's/.*"([^"]+)".*/\1/' \
    | sort -u
}

# pull_if_missing <base_url> <remote_model_list_text> <model>
#   echoes "skip" if already present, "pulled" if it had to fetch.
#   Uses the Ollama HTTP /api/pull endpoint so the pull happens on the
#   target host, not on whatever machine is running this script.
pull_if_missing() {
  local base="$1" models="$2" want="$3"
  if grep -Fxq "$want" <<<"$models"; then
    echo "skip"
    return 0
  fi
  # /api/pull streams NDJSON status updates; ignore the body and just
  # exit non-zero on connection / HTTP failure. Long timeout because
  # model pulls can take minutes.
  curl --silent --fail --max-time 1800 \
    -X POST "$base/api/pull" \
    -H 'Content-Type: application/json' \
    -d "{\"name\":\"$want\",\"stream\":false}" \
    > /dev/null
  echo "pulled"
}

# smoke <base_url> <model>  →  exit 0 if 1-token chat returns 200 with content
smoke() {
  local base="$1" model="$2"
  local resp
  resp=$(curl --silent --fail --max-time 60 \
           -H 'Content-Type: application/json' \
           -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word OK.\"}],\"stream\":false}" \
           "$base/api/chat")
  grep -q '"content":"OK"' <<<"$resp"
}

# upsert_env_var <file> <key> <value>
upsert_env_var() {
  local file="$1" key="$2" value="$3"
  if [[ ! -f "$file" ]]; then
    printf '%s=%s\n' "$key" "$value" >> "$file"
    return
  fi
  # Replace existing non-empty line, else append.
  if grep -qE "^${key}=" "$file"; then
    # Use a temp file to stay portable (BSD sed on macOS).
    local tmp
    tmp=$(mktemp)
    sed -E "s|^${key}=.*$|${key}=${value}|" "$file" > "$tmp"
    mv "$tmp" "$file"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$file"
  fi
}

# ---- mac --------------------------------------------------------------
run_mac() {
  hdr "Ollama-Mac @ http://localhost:11434"
  local base="http://localhost:11434"
  local failed=0

  if ! has_ollama_cli; then
    bad "ollama CLI not found in PATH"
    warn "install with:  curl -fsSL https://ollama.com/install.sh | sh"
    return 1
  fi
  ok "ollama CLI present ($(ollama --version 2>/dev/null || echo 'unknown'))"

  if probe_endpoint "$base"; then
    ok "endpoint reachable: $base"
  else
    bad "endpoint unreachable: $base"
    warn "is the Ollama daemon running?  brew services start ollama   # or:  ollama serve &"
    return 1
  fi

  local models
  models=$(list_remote_models "$base" || true)
  for m in "${MAC_REQUIRED_MODELS[@]}"; do
    case "$(pull_if_missing "$base" "$models" "$m")" in
      skip)   ok "model present:    $m" ;;
      pulled) ok "model pulled:     $m" ;;
      *)      bad "model pull failed: $m"; failed=1 ;;
    esac
  done

  if [[ $PULL_ONLY -eq 1 ]]; then
    return "$failed"
  fi

  for m in "${MAC_REQUIRED_MODELS[@]}"; do
    if smoke "$base" "$m"; then
      ok "smoke test:       $m"
    else
      bad "smoke test failed: $m"
      failed=1
    fi
  done

  return "$failed"
}

# ---- surface ----------------------------------------------------------
run_surface() {
  hdr "Ollama-Surface (target $SURFACE_IP_ARG)"
  local base
  if [[ -z "$SURFACE_IP_ARG" ]]; then
    # Reuse whatever is already in .env.
    if [[ -f "$ENV_FILE" ]] && grep -qE '^SURFACE_IP=.+' "$ENV_FILE"; then
      SURFACE_IP_ARG=$(grep -E '^SURFACE_IP=' "$ENV_FILE" | head -1 | sed -E 's/^SURFACE_IP=//')
      warn "--ip not given; reusing SURFACE_IP=$SURFACE_IP_ARG from .env"
    else
      bad "Surface IP not set"
      warn "pass it on the command line:  $0 --surface --ip 192.168.x.x"
      return 1
    fi
  fi

  base="http://$SURFACE_IP_ARG:11434"

  if ! has_ollama_cli; then
    bad "ollama CLI not found in PATH"
    warn "install with:  curl -fsSL https://ollama.com/install.sh | sh"
    return 1
  fi

  if probe_endpoint "$base"; then
    ok "endpoint reachable: $base"
  else
    bad "endpoint unreachable: $base"
    warn "is Ollama running on the Surface?  and listening on 0.0.0.0:11434?"
    return 1
  fi

  # Persist the IP so the registry can expand ${SURFACE_IP} on next boot.
  upsert_env_var "$ENV_FILE" "SURFACE_IP" "$SURFACE_IP_ARG"
  ok "wrote SURFACE_IP=$SURFACE_IP_ARG to $ENV_FILE"

  local models
  models=$(list_remote_models "$base" || true)
  local failed=0
  for m in "${SURFACE_REQUIRED_MODELS[@]}"; do
    case "$(pull_if_missing "$base" "$models" "$m")" in
      skip)   ok "model present:    $m" ;;
      pulled) ok "model pulled:     $m" ;;
      *)      bad "model pull failed: $m"; failed=1 ;;
    esac
  done

  if [[ $PULL_ONLY -eq 1 ]]; then
    return "$failed"
  fi

  for m in "${SURFACE_REQUIRED_MODELS[@]}"; do
    if smoke "$base" "$m"; then
      ok "smoke test:       $m"
    else
      bad "smoke test failed: $m"
      failed=1
    fi
  done

  return "$failed"
}

# ---- main -------------------------------------------------------------
exit_code=0

if [[ $DO_MAC -eq 1 ]]; then
  if run_mac; then :; else exit_code=1; fi
fi

if [[ $DO_SURFACE -eq 1 ]]; then
  if run_surface; then :; else exit_code=1; fi
fi

echo
if [[ $exit_code -eq 0 ]]; then
  log "${C_OK}all Ollama targets ready.${C_RST}"
else
  log "${C_BAD}one or more Ollama targets failed; see above.${C_RST}"
fi

exit $exit_code