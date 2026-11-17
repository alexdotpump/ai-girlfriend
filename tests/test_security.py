"""Owner authorization, input sanitising and rate limiting."""

from __future__ import annotations

import pytest

from leyla.errors import RateLimitedError, UnauthorizedError
from leyla.security import (
    SlidingWindowLimiter,
    is_owner,
    parse_memory_id,
    sanitize_user_text,
)


def test_is_owner_matches_only_configured_ids():
    assert is_owner(42, {42})
    assert is_owner("42", frozenset({42}))
    assert not is_owner(43, {42})
    assert not is_owner(None, {42})


def test_sanitize_strips_control_characters_and_bounds_length():
    dirty = "hi\x00\x07 there\n\n\n\nfriend"
    cleaned = sanitize_user_text(dirty, max_chars=50)
    assert "\x00" not in cleaned
    assert "\n\n\n" not in cleaned
    assert cleaned.endswith("friend")

    long_text = sanitize_user_text("a" * 500, max_chars=100)
    assert len(long_text) <= 101


def test_parse_memory_id_rejects_bad_values():
    assert parse_memory_id("#12") == 12
    for bad in ("abc", "0", "-3", ""):
        with pytest.raises(ValueError):
            parse_memory_id(bad)


def test_sliding_window_limiter_blocks_and_recovers():
    limiter = SlidingWindowLimiter(limit=2, window_seconds=10)
    limiter.check("42", now=100)
    limiter.check("42", now=101)
    with pytest.raises(RateLimitedError) as excinfo:
        limiter.check("42", now=102)
    assert excinfo.value.retry_after is not None
    # Outside the window it allows again.
    limiter.check("42", now=120)


def test_limiter_is_per_key():
    limiter = SlidingWindowLimiter(limit=1)
    limiter.check(1)
    limiter.check(2)
    with pytest.raises(RateLimitedError):
        limiter.check(1)


@pytest.mark.asyncio
async def test_owner_only_check_uses_bot_settings(settings):
    from types import SimpleNamespace

    from leyla.cogs.commands import AdminCog
    from leyla.security import is_owner as _is_owner

    bot = SimpleNamespace(settings=settings)
    cog = AdminCog(bot)

    allowed = SimpleNamespace(user=SimpleNamespace(id=42))
    assert await cog.interaction_check(allowed) is True
    assert _is_owner(allowed.user.id, settings.owner_ids)

    denied = SimpleNamespace(user=SimpleNamespace(id=999))
    with pytest.raises(UnauthorizedError):
        await cog.interaction_check(denied)
