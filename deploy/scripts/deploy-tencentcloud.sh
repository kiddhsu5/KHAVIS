#!/usr/bin/env bash
# Tencent Cloud Lighthouse / CVM deploy helper for khavis-bot.
#
# Prerequisites:
#   - tccli configured (`tccli configure`)
#   - TCR namespace created (Tencent Container Registry)
#   - CVM/Lighthouse instance reachable via SSH

set -euo pipefail

REGION="${TENCENTCLOUD_REGION:-ap-guangzhou}"
NAMESPACE="${TENCENTCLOUD_TCR_NAMESPACE:-khavis}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

echo "== Building image =="
docker build -t "$NAMESPACE/khavis-bot:$IMAGE_TAG" -f deploy/Dockerfile .

echo "== Pushing to TCR =="
REGISTRY="${TENCENTCLOUD_TCR_REGISTRY:-${NAMESPACE}.tencentcr.com}"
docker tag "$NAMESPACE/khavis-bot:$IMAGE_TAG" "$REGISTRY/$NAMESPACE/khavis-bot:$IMAGE_TAG"
docker push "$REGISTRY/$NAMESPACE/khavis-bot:$IMAGE_TAG"

echo "== Pulling on CVM + restarting =="
TENCENTCLOUD_VM_HOST="${TENCENTCLOUD_VM_HOST:-khavis-bot}"
ssh "$TENCENTCLOUD_VM_HOST" <<EOF
  set -eu
  cd ~/khavis-bot || git clone https://github.com/kiddhsu5/K.H.A.V.I.S..git khavis-bot
  cd khavis-bot
  git pull --ff-only
  docker pull $REGISTRY/$NAMESPACE/khavis-bot:$IMAGE_TAG
  docker compose -f deploy/docker-compose.yml --env-file .env up -d
EOF

echo "Done."