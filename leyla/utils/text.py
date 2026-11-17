"""Text utilities: Discord-safe chunking, mention stripping, formatting."""

from __future__ import annotations

import re

DISCORD_LIMIT = 2000


def chunk_text(text: str, limit: int = 1900) -> list[str]:
    """Split a long reply into Discord-sized chunks on natural boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        candidates = [
            window.rfind("\n\n"),
            window.rfind("\n"),
            window.rfind(". "),
            window.rfind("! "),
            window.rfind("? "),
            window.rfind(" "),
        ]
        split = max(candidates)
        if split < int(limit * 0.5):
            split = limit
        piece = remaining[:split].rstrip()
        if piece:
            chunks.append(piece)
        remaining = remaining[split:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks[:6]  # never spam more than six messages


def truncate(text: str, limit: int = 300, suffix: str = "…") -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def strip_bot_mention(content: str, bot_id: int | None) -> str:
    """Remove ``<@id>`` / ``<@!id>`` prefixes so mentions do not reach the model."""
    if not content:
        return ""
    text = content
    if bot_id:
        text = re.sub(rf"<@!?{bot_id}>", "", text)
    text = re.sub(r"<@&\d+>", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def looks_like_voice_request(text: str) -> bool:
    """Detect an explicit ask for a spoken reply."""
    lowered = (text or "").lower()
    patterns = (
        "voice note",
        "voice message",
        "voice memo",
        "say it out loud",
        "say that out loud",
        "speak it",
        "tell me in voice",
        "send a voice",
        "audio message",
        "read it to me",
    )
    return any(pattern in lowered for pattern in patterns)
