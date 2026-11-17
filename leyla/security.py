"""Privacy and access control.

Leyla is a *private* companion: every conversational surface is gated on the
configured owner Discord ID. Nothing in this module reads secrets from source.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import defaultdict, deque
from collections.abc import Iterable

import discord
from discord import app_commands

from .errors import RateLimitedError, UnauthorizedError

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def is_owner(user_id: int | None, owner_ids: Iterable[int]) -> bool:
    """True when ``user_id`` is one of the configured owners."""
    if user_id is None:
        return False
    try:
        return int(user_id) in set(int(o) for o in owner_ids)
    except (TypeError, ValueError):
        return False


def sanitize_user_text(text: str, *, max_chars: int = 2000) -> str:
    """Normalise and bound untrusted user input before it reaches the model."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_CHARS.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def parse_memory_id(raw: str) -> int:
    """Validate a memory ID supplied through a command."""
    try:
        value = int(str(raw).strip().lstrip("#"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Memory ID must be a number") from exc
    if value <= 0:
        raise ValueError("Memory ID must be positive")
    return value


class SlidingWindowLimiter:
    """Per-key sliding-window limiter (in-memory, per process).

    Deliberately tiny: a handful of owners, so a deque per key is plenty.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = max(1, int(limit))
        self.window = float(window_seconds)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str | int, *, now: float | None = None) -> None:
        """Raise :class:`RateLimitedError` when ``key`` is over its budget."""
        now = time.monotonic() if now is None else now
        bucket = self._hits[str(key)]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            retry_after = max(0.0, self.window - (now - bucket[0]))
            raise RateLimitedError(
                f"rate limit reached for {key} ({self.limit}/{self.window:.0f}s)",
                retry_after=retry_after,
            )
        bucket.append(now)

    def reset(self, key: str | int | None = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(str(key), None)


def owner_only():
    """``app_commands`` check: only configured owners may run the command."""

    async def predicate(interaction: discord.Interaction) -> bool:
        bot = interaction.client
        owner_ids = getattr(getattr(bot, "settings", None), "owner_ids", frozenset())
        if not is_owner(interaction.user.id if interaction.user else None, owner_ids):
            raise UnauthorizedError("non-owner interaction rejected")
        return True

    return app_commands.check(predicate)


def is_dm(interaction_or_message) -> bool:
    guild = getattr(interaction_or_message, "guild", None)
    return guild is None
