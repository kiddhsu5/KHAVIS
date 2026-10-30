"""Read the historical ``telegram-pairing.json`` (allowlist + metadata).

The token is NOT stored here; only the chat-ID allowlist and metadata.
This loader is best-effort: if the file is missing or malformed we
return empty data and the bot falls back to whatever was set in env.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SEARCH_PATHS: tuple[str, ...] = (
    str(Path.home() / ".config" / "khavis" / "telegram-pairing.json"),
    "/etc/khavis/telegram-pairing.json",
)


@dataclass
class PairingData:
    allow_from: list[int] = field(default_factory=list)
    bot_username: str = ""  # type: ignore[type-arg]


def load_pairing(extra_search_paths: tuple[str, ...] = ()) -> PairingData:
    """Search for a known ``telegram-pairing.json`` and parse it."""
    candidates: list[Path] = []
    for env_var in ("TELEGRAM_PAIRING_PATH",):
        p = os.environ.get(env_var)
        if p:
            candidates.append(Path(p))
    candidates.extend(Path(p) for p in DEFAULT_SEARCH_PATHS)
    candidates.extend(Path(p) for p in extra_search_paths)

    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            allow = data.get("allowFrom") or []
            allow_ids: list[int] = []
            for entry in allow:
                if isinstance(entry, int):
                    allow_ids.append(entry)
                elif isinstance(entry, dict) and "id" in entry:
                    with contextlib.suppress(TypeError, ValueError):
                        allow_ids.append(int(entry["id"]))
            return PairingData(
                bot_username=str(data.get("bot_username") or data.get("username") or ""),
                allow_from=allow_ids,
            )
    return PairingData()
