"""Render a ``DispatchReport`` as a single plain-text Telegram message."""

from __future__ import annotations

from .models import DispatchReport
from .telegram_api import TelegramAPI


def format_report(report: DispatchReport) -> str:
    """Single-message plain-text view of per-backend outcomes + consensus.

    Sent with ``parse_mode=None`` — see ``TelegramAPI.format_result_block``
    for why nothing here is MarkdownV2-escaped.
    """
    prompt = _truncate(report.envelope.prompt, 200)
    lines: list[str] = [f"🔁 /run: {prompt}", ""]
    for r in report.results:
        lines.append(TelegramAPI.format_result_block(r))
        lines.append("")
    if report.consensus:
        src = str(report.consensus_source)
        body = _truncate(report.consensus, 3000)
        lines.append(f"🏁 consensus (from {src}):\n{body}")
    else:
        lines.append("⚠️ no backend produced a usable answer")
    text = "\n".join(lines).rstrip()
    # Telegram has a 4096-char hard limit per message; the per-block
    # 1500-char cap keeps us under it for any realistic reply.
    return text[:4000]


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"
