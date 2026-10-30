"""Telegram dispatch bot for khavis.

Sub-modules:
- ``config``     load token, allowed chat IDs, timeouts
- ``secrets``    dataclass holding the bot token + allowlist
- ``models``     pydantic models for messages, envelopes, results
- ``telegram_api``  thin async wrapper over the Bot HTTP API
- ``handlers``   command router (start, help, status, run, pools)
- ``dispatch``   fan-out orchestration across backends
- ``aggregator`` synthesize consensus from per-backend results
- ``polling``    long-poll loop (fallback path)
- ``webhook``    webhook route handler
- ``main``       FastAPI app + lifespan + console-script entry point

Tokens are NEVER hard-coded. They come from the ``BOT_TOKEN`` environment
variable (set by the operator / docker-compose), with the historical
allowlist and chat-id metadata loaded from a sidecar JSON file via
``pairing_loader``. Secrets never touch this file system.
"""

from __future__ import annotations
