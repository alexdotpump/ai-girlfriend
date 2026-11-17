"""Long-term memory behaviour."""

from __future__ import annotations

import pytest

from leyla.memory.store import MemoryStore, fingerprint, tokenize


@pytest.mark.asyncio
async def test_add_and_deduplicate(db):
    store = MemoryStore(db)
    memory, created = await store.add("42", "Their dog is called Pixel", kind="person", importance=0.8)
    assert created and memory is not None
    assert memory.kind == "person"

    duplicate, created_again = await store.add("42", "their dog is called Pixel ", kind="person")
    assert created_again is False
    assert duplicate is not None and duplicate.id == memory.id

    assert await store.count("42") == 1


@pytest.mark.asyncio
async def test_search_ranks_relevant_memories(db):
    store = MemoryStore(db)
    await store.add("42", "Works night shifts at a hospital", kind="fact", importance=0.7)
    await store.add("42", "Their favourite band is Slowdive", kind="preference", importance=0.6)
    await store.add("42", "Allergic to peanuts", kind="boundary", importance=0.95)

    results = await store.search("42", "what band should I listen to tonight")
    assert results, "expected at least one match"
    top, _score = results[0]
    assert "Slowdive" in top.content

    allergy = await store.search("42", "peanuts")
    assert any("peanuts" in memory.content for memory, _ in allergy)


@pytest.mark.asyncio
async def test_search_is_scoped_per_owner(db):
    store = MemoryStore(db)
    await store.add("42", "secret fact about owner", importance=0.9)
    assert await store.search("99", "secret fact") == []


@pytest.mark.asyncio
async def test_update_supersede_delete_and_clear(db):
    store = MemoryStore(db)
    memory, _ = await store.add("42", "Uses an iPhone", kind="fact", importance=0.6)
    assert memory is not None

    updated = await store.update("42", memory.id, content="Uses an Android phone now")
    assert updated is not None and "Android" in updated.content

    replacement, created = await store.supersede(
        "42", memory.id, "Uses a Pixel 9", kind="fact", importance=0.7
    )
    assert created and replacement is not None
    assert await store.get("42", memory.id) is None

    assert await store.delete("42", replacement.id) is True
    assert await store.delete("42", replacement.id) is False

    await store.add("42", "One more thing", importance=0.5)
    removed = await store.clear("42")
    assert removed == 1
    assert await store.count("42") == 0


@pytest.mark.asyncio
async def test_memory_cap_prunes_weakest(db):
    store = MemoryStore(db, max_per_scope=5)
    for index in range(8):
        await store.add("42", f"fact number {index}", importance=0.1 + index * 0.1)
    memories = await store.list("42", limit=20)
    assert len(memories) == 5
    assert all(m.importance >= 0.4 for m in memories)


@pytest.mark.asyncio
async def test_invalid_and_tiny_memories_are_rejected(db):
    store = MemoryStore(db)
    memory, created = await store.add("42", "a")
    assert memory is None and created is False
    # Unknown kinds are coerced, not stored blindly.
    stored, created = await store.add("42", "likes rainy mornings", kind="nonsense")
    assert created and stored is not None and stored.kind == "fact"


def test_fingerprint_and_tokenize():
    assert fingerprint("Same thing") == fingerprint("  same   THING ")
    assert fingerprint("a", "subject") != fingerprint("a", "other")
    tokens = tokenize("I really don't like loud restaurants")
    assert "restaurants" in tokens
    assert "really" not in tokens


@pytest.mark.asyncio
async def test_duplicate_across_kinds_is_still_deduped(db):
    store = MemoryStore(db)
    await store.add("42", "Birthday is 3 March", kind="date", importance=0.9)
    _, created = await store.add("42", "birthday is 3 march", kind="fact")
    assert created is False
    assert await store.count("42") == 1
