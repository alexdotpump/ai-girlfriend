"""SQLite schema, persistence and restart behaviour."""

from __future__ import annotations

import asyncio

import pytest

from leyla.db import Database
from leyla.memory.store import MemoryStore
from leyla.mood.engine import MoodEngine, MoodSuggestion
from leyla.mood.models import Mood
from leyla.preferences import PreferenceStore
from leyla.services import Services


@pytest.mark.asyncio
async def test_schema_and_usage_counters(db):
    version = await db.scalar("SELECT value FROM meta WHERE key='schema_version'")
    assert version == "1"

    day = "2026-11-17"
    await db.increment_usage(day, "llm", 1)
    await db.increment_usage(day, "llm", 2)
    assert await db.get_usage(day, "llm") == 3

    await db.set_cooldown("voice:x", 60)
    assert await db.get_cooldown("voice:x") > 0
    assert await db.get_cooldown("voice:none") == 0


@pytest.mark.asyncio
async def test_preferences_persist_and_validate(db, settings):
    store = PreferenceStore(db, settings)
    prefs = await store.get("42", 42)
    assert prefs.voice_mode == settings.voice_mode

    await store.set_voice_mode("42", 42, "request")
    await store.set_voice_frequency("42", 42, 0.75)
    await store.set_images_enabled("42", 42, False)

    again = await PreferenceStore(db, settings).get("42", 42)
    assert again.voice_mode == "request"
    assert again.voice_frequency == pytest.approx(0.75)
    assert again.images_enabled is False

    with pytest.raises(ValueError):
        await store.set_voice_mode("42", 42, "telepathy")


@pytest.mark.asyncio
async def test_services_survive_restart(settings):
    first = await Services.create(settings)
    await first.memories.add("42", "prefers oolong tea over coffee", kind="preference", importance=0.9)
    await first.mood.apply("42", MoodSuggestion(Mood.SERIOUS, 0.9, "hard conversation"))
    await first.conversations.add("42", "user", "my cat is called Miso", message_id="100")
    await first.db.increment_usage("2026-11-17", "llm", 5)
    await first.aclose()

    second = await Services.create(settings)
    memories = await second.memories.list("42")
    assert any("oolong" in m.content for m in memories)

    mood = await second.mood.get("42")
    assert mood.mood is Mood.SERIOUS

    recent = await second.conversations.recent("42")
    assert recent[-1]["content"] == "my cat is called Miso"
    assert await second.conversations.seen_message("42", "100") is True
    assert await second.db.get_usage("2026-11-17", "llm") == 5
    await second.aclose()


@pytest.mark.asyncio
async def test_database_close_is_idempotent(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    db.connect()
    await db.execute("INSERT INTO meta(key, value) VALUES('k','v')")
    db.close()
    db.close()


@pytest.mark.asyncio
async def test_wal_mode_is_enabled(db):
    mode = await db.scalar("PRAGMA journal_mode")
    assert str(mode).lower() == "wal"


def test_store_and_engine_share_the_same_connection(settings):
    # A simple guard against accidentally constructing two Database objects.
    db = Database(settings.db_path)
    db.connect()
    store = MemoryStore(db)
    engine = MoodEngine(db)
    assert store.db is engine.db
    db.close()
