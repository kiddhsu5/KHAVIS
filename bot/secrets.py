"""Bot secrets: token + allowlist + timeouts.

Tokens are sourced from environment variables only; ``pairing_loader`` is a
separate helper that reads the *historical* pairing JSON (which holds the
allowlist of chat IDs and any cached metadata) but never holds the token
itself in source-controlled files.
"""

from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass, field

# Telegram ``setWebhook(secret_token=...)`` only accepts 1-256 chars of
# ``A-Z a-z 0-9 _ -``; anything else is rejected by the Bot API, so we fail
# fast at startup instead of silently running with a secret Telegram can
# never send back.
_SECRET_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")


def validate_webhook_secret(secret: str) -> str:
    """Return ``secret`` if it is a valid Telegram ``secret_token``; raise otherwise."""
    if not secret:
        raise RuntimeError(
            "WEBHOOK_SECRET is required in webhook mode. Generate one with "
            "`python -c 'import secrets; print(secrets.token_urlsafe(32))'` and "
            "pass the same value to setWebhook as secret_token=..."
        )
    if not _SECRET_TOKEN_RE.fullmatch(secret):
        raise RuntimeError(
            "WEBHOOK_SECRET must be 1-256 characters of [A-Za-z0-9_-] "
            "(Telegram secret_token rules); check for quotes, spaces or an "
            "inline comment in your .env."
        )
    return secret


@dataclass
class BotSecrets:
    """Runtime credentials and limits for the dispatch bot."""

    bot_token: str
    webhook_secret: str  # sent as X-Telegram-Bot-Api-Secret-Token ("" in polling mode)
    allowed_chat_ids: list[int] = field(default_factory=list)
    backend_timeout_s: int = 180
    long_poll_timeout_s: int = 30

    @classmethod
    def from_env(cls, mode: str | None = None) -> BotSecrets:
        """Build from env. ``mode`` defaults to ``$BOT_MODE`` (``webhook``).

        Webhook mode requires a non-empty, Telegram-valid ``WEBHOOK_SECRET``;
        it no longer falls back to ``BOT_TOKEN`` (the token contains ``:``,
        which Telegram rejects as a secret_token, and reusing it widens the
        blast radius of a leak). Polling mode never receives inbound HTTP,
        so the secret is optional there.
        """
        mode = (mode or os.environ.get("BOT_MODE", "webhook")).strip().lower()
        token = os.environ.get("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "BOT_TOKEN environment variable is required. "
                "Obtain it from @BotFather and inject via .env or "
                "docker-compose, never commit it to source."
            )
        secret = os.environ.get("WEBHOOK_SECRET", "").strip()
        if mode != "polling":
            validate_webhook_secret(secret)
        allowed = _parse_allowed_chat_ids(os.environ.get("ALLOWED_CHAT_IDS", ""))
        timeout = int(os.environ.get("BACKEND_TIMEOUT_S", "180"))
        poll = int(os.environ.get("LONG_POLL_TIMEOUT_S", "30"))
        return cls(
            bot_token=token,
            webhook_secret=secret,
            allowed_chat_ids=allowed,
            backend_timeout_s=timeout,
            long_poll_timeout_s=poll,
        )


def _parse_allowed_chat_ids(raw: str) -> list[int]:
    """Parse ``"123,456,789"`` into ``[123, 456, 789]``."""
    out: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        with contextlib.suppress(ValueError):
            out.append(int(chunk))
    return out
