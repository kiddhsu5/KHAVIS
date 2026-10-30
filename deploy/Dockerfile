# Dockerfile for the K.H.A.V.I.S. Telegram dispatch bot.
#
# Build:   docker build -t khavis-bot -f deploy/Dockerfile .
# Run:     docker compose -f deploy/docker-compose.yml up -d
#
# The image ships:
#   - the K.H.A.V.I.S. source tree (so the bot can import `core`/`providers`/`agents`)
#   - the `bot/` package wired in
#   - Caddy for TLS termination in front of the FastAPI webhook server
#
# Secrets (BOT_TOKEN, WEBHOOK_SECRET, ALLOWED_CHAT_IDS) come from the
# docker-compose environment, never from this image.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install the project + bot extras. No editable install — keeps the
# image small and avoids hatchling build issues during container build.
COPY pyproject.toml requirements.txt ./
COPY core ./core
COPY providers ./providers
COPY bot ./bot
COPY config ./config
COPY agents ./agents
COPY server ./server
COPY web ./web
COPY scripts ./scripts

RUN pip install --no-cache-dir \
        pyyaml watchdog openai google-generativeai requests pydantic python-dotenv \
        'fastapi>=0.110' 'uvicorn[standard]>=0.27' 'httpx>=0.27' 'python-telegram-bot==20.7'

# Caddy for TLS termination.
FROM caddy:2.8-alpine AS caddy

# Final image: Python runtime + Caddy.
FROM base
COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY deploy/certs /etc/caddy/certs  # mounted at deploy time
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O - http://localhost:8080/healthz | grep -q '"status"' || exit 1

ENTRYPOINT ["/entrypoint.sh"]
