#!/bin/sh
# Entrypoint for the khavis-bot container.
# Starts Caddy in the background (TLS termination + reverse proxy),
# then runs the FastAPI webhook server in the foreground.

set -eu

# Start Caddy as a daemon.
caddy run --config /etc/caddy/Caddyfile &
CADDY_PID=$!

# Trap signals so Caddy dies with us.
trap 'kill $CADDY_PID 2>/dev/null || true' EXIT TERM INT

# Run the bot. uvicorn binds to 127.0.0.1 — only Caddy exposes :443.
exec python -m bot.main \
    --mode webhook \
    --host 127.0.0.1 \
    --port 8080