#!/usr/bin/env bash
# Aliyun ECS / Lighthouse deploy helper for khavis-bot.
#
# Prerequisites (run once):
#   - aliyun CLI installed: `aliyun configure` with your AccessKey
#   - Container Registry (CR) namespace created in the
#     registry.console.aliyun.com console
#   - ECS / Lighthouse instance reachable via SSH
#     (set ALIYUN_ECS_HOST or pass --host)
#   - Domain ``bot.example.com`` resolves to the ECS public IP
#     (add an A record in your DNS provider)
#   - Inbound 80/443 open in the security group (for ACME HTTP-01)
#
# Usage:
#   ALIYUN_ECS_HOST=root@<public-ip> bash scripts/deploy-aliyun.sh
#
# Idempotent: re-run after each khavis-bot release. The remote
# workflow is `git pull → docker pull → docker compose up -d`.

set -euo pipefail

REGION="${ALIYUN_REGION:-cn-hangzhou}"
NAMESPACE="${ALIYUN_CR_NAMESPACE:-khavis}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
ALIYUN_ECS_HOST="${ALIYUN_ECS_HOST:-}"

if [[ -z "$ALIYUN_ECS_HOST" ]]; then
    cat >&2 <<EOF
ERROR: ALIYUN_ECS_HOST is not set. Example:
  ALIYUN_ECS_HOST=root@1.2.3.4 bash scripts/deploy-aliyun.sh

If your SSH key needs a different user or port, encode them in the
host string: 'user@host' or 'user@host:2222'.
EOF
    exit 2
fi

# ---------------------------------------------------------------------------
# Step 1: build the image locally
# ---------------------------------------------------------------------------
echo "== building image (tag=$IMAGE_TAG) =="
docker build -t "$NAMESPACE/khavis-bot:$IMAGE_TAG" -f deploy/Dockerfile .

# ---------------------------------------------------------------------------
# Step 2: tag + push to Aliyun Container Registry (ACR)
# ---------------------------------------------------------------------------
REGISTRY="${ALIYUN_REGISTRY:-${NAMESPACE}.registry.aliyuncs.com}"
echo "== pushing to ACR (${REGISTRY}/${NAMESPACE}/khavis-bot:$IMAGE_TAG) =="
docker tag "$NAMESPACE/khavis-bot:$IMAGE_TAG" "$REGISTRY/$NAMESPACE/khavis-bot:$IMAGE_TAG"
docker push "$REGISTRY/$NAMESPACE/khavis-bot:$IMAGE_TAG"

# ---------------------------------------------------------------------------
# Step 3: SSH into the ECS instance and roll the deployment
# ---------------------------------------------------------------------------
echo "== rolling out on $ALIYUN_ECS_HOST =="
ssh "$ALIYUN_ECS_HOST" <<EOF
    set -eu
    cd ~/khavis-bot 2>/dev/null || {
        echo "first deploy: cloning repo"
        git clone https://github.com/kiddhsu5/K.H.A.V.I.S..git ~/khavis-bot
        cd ~/khavis-bot
    }
    git pull --ff-only

    # Pull the new image.
    docker pull ${REGISTRY}/${NAMESPACE}/khavis-bot:${IMAGE_TAG}

    # Build / write .env on the server. The operator ships their own
    # .env out-of-band (e.g. via scp, ansible-vault, secrets manager).
    # Here we just refuse to start if .env is missing.
    if [ ! -f .env ]; then
        echo "ERROR: .env not found on the remote. Copy it first:" >&2
        echo "  scp .env ${ALIYUN_ECS_HOST}:~/khavis-bot/.env" >&2
        exit 3
    fi

    cd ~/khavis-bot
    docker compose -f deploy/docker-compose.yml --env-file .env up -d
    sleep 3
    curl -fsS http://127.0.0.1:8080/healthz && echo " → healthy"
EOF

echo
echo "Done. Set webhook with:"
echo "  curl -sS \"https://api.telegram.org/bot\${BOT_TOKEN}/setWebhook\" -d \"url=https://bot.example.com/webhook\" -d \"secret_token=\${WEBHOOK_SECRET}\""