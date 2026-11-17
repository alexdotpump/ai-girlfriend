"""Short-term context window."""

from __future__ import annotations

import pytest

from leyla.memory.context import MAX_ROWS_PER_SCOPE, ConversationStore


@pytest.mark.asyncio
async def test_recent_returns_chronological_window(db):
    store = ConversationStore(db)
    await store.add("42", "user", "first")
    await store.add("42", "assistant", "second")
    await store.add("42", "user", "third")

    recent = await store.recent("42", limit=2)
    assert [m["content"] for m in recent] == ["second", "third"]
    assert recent[-1]["role"] == "user"


@pytest.mark.asyncio
async def test_message_ids_dedupe_and_clear(db):
    store = ConversationStore(db)
    await store.add("42", "user", "hello", message_id=777)
    assert await store.seen_message("42", 777) is True
    assert await store.seen_message("42", 778) is False

    removed = await store.clear("42")
    assert removed == 1
    assert await store.seen_message("42", 777) is False


@pytest.mark.asyncio
async def test_window_is_bounded(db):
    store = ConversationStore(db)
    for index in range(MAX_ROWS_PER_SCOPE + 25):
        await store.add("42", "user", f"message {index}")
    stats = await store.stats("42")
    assert stats["count"] <= MAX_ROWS_PER_SCOPE
    recent = await store.recent("42", limit=MAX_ROWS_PER_SCOPE + 10)
    assert recent[-1]["content"].startswith(f"message {MAX_ROWS_PER_SCOPE + 24}")


@pytest.mark.asyncio
async def test_recent_topics_truncates(db):
    store = ConversationStore(db)
    await store.add("42", "user", "x" * 300)
    topics = await store.recent_topics("42")
    assert topics and len(topics[0]) <= 91


@pytest.mark.asyncio
async def test_invalid_role_rejected(db):
    store = ConversationStore(db)
    with pytest.raises(ValueError):
        await store.add("42", "system", "nope")
