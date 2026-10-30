# Telegram dispatch bot

The `bot/` package turns the K.H.A.V.I.S. project into a Telegram bot that
fans out a single user prompt to **multiple LLM backends in parallel**
and reports the synthesized result back to the chat. The architecture
is described in `/.claude/plans/keen-percolating-ladybug.md` (executed
2026-09-21). This doc is the operator-facing reference.

## At a glance

```
Telegram ──webhook──▶ Caddy (TLS) ──▶ uvicorn (FastAPI)
                                              │
                  ┌───────────────────────────┼────────────────────────────┐
                  ▼                           ▼                            ▼
          ClaudeBackend            CodexBackend              KhavisBackend
          `claude -p "..."`        `codex exec "..."`         in-process pool lookup
                  │                           │                            │
                  └─────────▶ Aggregator ◀────┴─────────▶ Telegram sendMessage
```

`KhavisBackend` reuses `core.registry.PluginRegistry` + `apply_pools_config` so
the bot picks whichever pool the capability hint selects. No new HTTP
plumbing for the gateway itself — the bot calls `plugin.chat()` in-process.

## Install

```bash
cd /path/to/khavis
pip install -e .[bot]
# or, without editable:
pip install fastapi 'uvicorn[standard]' httpx 'python-telegram-bot==20.7'
```

Python ≥ 3.11 required (project baseline).

## Run locally — polling mode

Simplest dev setup; no public endpoint, no TLS, no Caddy.

```bash
BOT_TOKEN=...                              # from @BotFather
ALLOWED_CHAT_IDS=123456789                 # your numeric chat ID
python -m bot.main --mode polling
```

The bot calls `getUpdates` in a loop and routes commands through the
same handler pipeline that the webhook uses.

## Run locally — webhook mode

For testing the webhook path with a real Telegram setup you need a
public HTTPS URL. Easy options: `ngrok http 8080`, or Cloudflare
tunnel. Then:

```bash
export WEBHOOK_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
BOT_TOKEN=... ALLOWED_CHAT_IDS=... PUBLIC_URL=https://xyz.ngrok.io \
python -m bot.main --mode webhook --port 8080

# One-shot registration (secret_token must equal WEBHOOK_SECRET):
curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -d "url=${PUBLIC_URL}/webhook" -d "secret_token=${WEBHOOK_SECRET}"
```

## Commands

| Command | Behavior |
|---|---|
| `/start` | Welcome + allowlist check |
| `/help` | Usage |
| `/status` | Per-backend health snapshot |
| `/pools` | K.H.A.V.I.S. pool list with capability tags |
| `/run <prompt>` | Fan out to claude, codex, K.H.A.V.I.S. |
| `/run --only codex,claude <prompt>` | Subset |
| `/run --capability code <prompt>` | Hint for the gateway's `CapabilityRouter` |
| `/phase <plan\|code\|review\|verify\|pipeline> <prompt>` | Phase 4 staged run (multi-agent pipeline) |
| `/history` | Recent dispatches / phase runs |

## Backend behavior

- **`ClaudeBackend`** shells out: `claude -p --output-format json [--model <m>] <prompt>`. Parses JSON for `text` / `tokens_in` / `tokens_out`; falls back to raw stdout if JSON is absent.
- **`CodexBackend`** shells out: `codex exec --json [-m <m>] <prompt>`. Retries without `--json` if the flag isn't accepted by your Codex version.
- **`KhavisBackend`** is in-process: looks up the first healthy plugin with the requested capability, calls `plugin.chat()` in a thread executor (so the event loop stays free), returns the canonical OpenAI-style response.

Hard timeout per backend: `BACKEND_TIMEOUT_S` (default 180 s). Subprocesses are killed on timeout.

## Result format

A single Telegram message, edited as results arrive:

```
🔁 /run: refactor tests/test_registry.py for parametrization

✅ claude (claude-sonnet-5, 12.4s · in=412 out=88)
1.7 KB of suggested patch…
✅ codex (gpt-5, 8.9s · in=380 out=64)
1.5 KB of suggested patch…
✅ khavis (Ollama-Mac / gemma4:e2b, 15.2s · in=401 out=72)
1.6 KB of suggested patch…

🏁 consensus (from `claude`):
<shortest clean answer among the three>
```

Synthesis is heuristic (longest non-empty answer ≥ 20 chars wins).
A judge-model upgrade is deferred.

## Configuration

All via env vars (read by `bot.secrets.BotSecrets.from_env()`):

| Variable | Default | Purpose |
|---|---|---|
| `BOT_TOKEN` | required | Telegram bot token |
| `WEBHOOK_SECRET` | required (webhook mode) | Value of `X-Telegram-Bot-Api-Secret-Token`; 1-256 chars of `[A-Za-z0-9_-]`, no BOT_TOKEN fallback. Unused in polling mode |
| `BOT_MAX_CONCURRENCY` | `4` | Updates processed in parallel (background tasks) |
| `BOT_USERNAME` | `""` | When set, `/cmd@OtherBot` in groups is ignored |
| `ALLOWED_CHAT_IDS` | `""` (deny all) | Comma-separated numeric chat IDs allowed to invoke |
| `BOT_MODE` | `webhook` | `webhook` or `polling` |
| `BOT_HOST` | `0.0.0.0` | webhook bind host |
| `BOT_PORT` | `8080` | webhook bind port |
| `BACKEND_TIMEOUT_S` | `180` | Hard timeout per backend call |
| `LONG_POLL_TIMEOUT_S` | `30` | getUpdates `timeout` value |
| `CLAUDE_DISPATCH_MODEL` | `""` (CLI default) | `--model` flag for claude |
| `CODEX_DISPATCH_MODEL` | `""` (CLI default) | `-m` flag for codex |
| `TELEGRAM_PAIRING_PATH` | (auto-detect) | Path to historical `telegram-pairing.json` |

## Allowlist

The `ALLOWED_CHAT_IDS` env var is the authoritative source. If it's
empty, **no one** can use the bot — there is no permissive default. An
optional `telegram-pairing.json` from `~/.config/khavis/` is
auto-loaded and merged in for operator convenience.

## Cloud deployment

See `deploy/README.md` for the Docker + Caddy + Aliyun/TencentCloud
flow. The TL;DR:

```bash
cp deploy/.env.example .env            # fill in BOT_TOKEN etc.
docker build -t khavis-bot -f deploy/Dockerfile .
docker compose -f deploy/docker-compose.yml --env-file .env up -d
curl https://bot.example.com/healthz
```

## Tests

```bash
PYTHONPATH=. BOT_TOKEN=test pytest tests/test_bot_*.py -v
```

26 unit tests cover command parsing, MarkdownV2 escaping, synthesis
heuristics, and report formatting. Integration tests against real
Telegram / claude / codex are not bundled — exercise them manually
once the bot is deployed.

## Out of scope (deliberately deferred)

- Real streaming replies (each backend's `--output-format stream-json`)
- Judge model replacing the longest-wins synthesis
- Multi-user RBAC beyond the allowlist
- HTTP service for the K.H.A.V.I.S. gateway itself (the bot imports it in-process)