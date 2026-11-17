"""Memory recall quality: word forms, exact phrasing, thread context, diversity."""

from __future__ import annotations

import pytest

from leyla.memory.store import MemoryStore, bigrams, token_hits, tokenize


def test_token_hits_tolerate_word_forms():
    query = tokenize("what are your dogs called")
    assert token_hits(query, tokenize("Their dog is called Pixel")) >= 2
    # Short words must not match by prefix (car vs cartoon).
    assert token_hits(tokenize("car"), tokenize("cartoon marathon")) == 0


def test_bigrams_ignore_filler_words():
    assert "dentist appointment" in bigrams("I have a dentist appointment on Thursday")


@pytest.mark.asyncio
async def test_word_form_and_phrase_matches_rank_first(db):
    store = MemoryStore(db)
    await store.add("42", "Their dog is called Pixel", kind="person", importance=0.6)
    await store.add("42", "They walk to work most mornings", kind="fact", importance=0.6)

    dogs = await store.search("42", "what are your dogs called again")
    assert dogs and "Pixel" in dogs[0][0].content

    walk = await store.search("42", "do you still walk to work")
    assert walk and "walk to work" in walk[0][0].content


@pytest.mark.asyncio
async def test_thread_context_recalls_a_fact_from_earlier_messages(db):
    store = MemoryStore(db)
    await store.add("42", "Has a dentist appointment on Thursday", kind="date", importance=0.7)
    await store.add("42", "Favourite film is Arrival", kind="preference", importance=0.5)

    results = await store.search(
        "42",
        "what time is that again?",
        context="remind me about the dentist thing\nokay so Thursday then",
    )
    assert results and "dentist" in results[0][0].content


@pytest.mark.asyncio
async def test_results_stay_diverse_for_one_subject(db):
    store = MemoryStore(db)
    for index in range(5):
        await store.add("42", f"Gym routine detail {index} about lifting", kind="topic", importance=0.5)
    await store.add("42", "Gym membership ends in March", kind="date", importance=0.9)
    await store.add("42", "Sister's name is Mina", kind="person", importance=0.9)

    results = await store.search("42", "gym lifting routine", limit=3)
    contents = [memory.content for memory, _ in results]
    assert len(contents) == 3
    assert "Mina" in " ".join(contents)


@pytest.mark.asyncio
async def test_boundaries_are_never_dropped_from_recall(db):
    store = MemoryStore(db)
    await store.add("42", "Allergic to peanuts", kind="boundary", importance=0.9)
    await store.add("42", "Likes rainy Sundays", kind="preference", importance=0.3)

    results = await store.search("42", "what should we order for dinner")
    assert any("peanuts" in memory.content for memory, _ in results)
