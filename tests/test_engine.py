"""Personality prompt, envelope parsing and the conversation engine."""

from __future__ import annotations

import json

import pytest

from leyla.ai.engine import ConversationEngine, clean_reply, parse_turn_payload
from leyla.ai.registry import ProviderChain
from leyla.errors import BudgetExceededError, ProviderError
from leyla.memory.context import ConversationStore
from leyla.memory.extractor import MemoryExtractor, parse_candidates
from leyla.memory.store import MemoryStore
from leyla.mood.engine import MoodEngine
from leyla.mood.models import Mood
from leyla.personality.profile import PERSONA_CORE, build_system_prompt

from conftest import ScriptedProvider


def _engine(settings, db, provider):
    store = MemoryStore(db)
    conversations = ConversationStore(db)
    mood = MoodEngine(db)
    chain = ProviderChain([provider])
    extractor = MemoryExtractor(chain)
    return (
        ConversationEngine(settings, chain, db, store, conversations, mood, extractor),
        store,
        conversations,
    )


def test_prompt_contains_identity_rules_and_contract():
    prompt = build_system_prompt(
        mood=None,
        memories=None,
        display_name="Alex",
        local_time="Monday 21:14",
        voice_enabled=False,
    )
    assert "You are Leyla" in prompt
    assert "Never start with" in prompt
    assert '"reply"' in prompt
    assert "VOICE NOTES ARE DISABLED" in prompt
    assert "Alex" in prompt
    assert PERSONA_CORE.splitlines()[0] in prompt


def test_prompt_includes_memories_with_ids():
    class Memory:
        def __init__(self, id, kind, content):
            self.id, self.kind, self.content = id, kind, content

    prompt = build_system_prompt(
        mood=None,
        memories=[(Memory(7, "preference", "prefers tea over coffee"), 0.9)],
        display_name="Alex",
    )
    assert "#7" in prompt
    assert "prefers tea over coffee" in prompt


def test_parse_turn_payload_valid_json_and_fences():
    payload = {
        "reply": "oh, we're doing this again?",
        "mood": {"name": "playful", "intensity": 0.7, "why": "teasing"},
        "voice": {"send": True, "energy": "high", "delivery": "amused"},
        "remember": [{"kind": "fact", "content": "Their sister is called Nina", "importance": 0.6}],
    }
    parsed = parse_turn_payload("```json\n" + json.dumps(payload) + "\n```")
    assert parsed.parsed is True
    assert parsed.reply.startswith("oh,")
    assert parsed.mood is not None and parsed.mood.mood is Mood.PLAYFUL
    assert parsed.voice.send is True and parsed.voice.energy == "high"
    assert parsed.memories and "Nina" in parsed.memories[0].content


def test_parse_turn_payload_plain_text_fallback():
    parsed = parse_turn_payload("Leyla: just text, no envelope.")
    assert parsed.parsed is False
    assert parsed.reply == "just text, no envelope."
    assert parsed.mood is None
    assert parsed.memories == []


def test_parse_turn_payload_rejects_bad_mood_names():
    parsed = parse_turn_payload('{"reply": "hey", "mood": {"name": "feral", "intensity": 2}}')
    assert parsed.reply == "hey"
    assert parsed.mood is None


def test_parse_turn_payload_clamps_and_validates_candidates():
    parsed = parse_turn_payload(
        json.dumps(
            {
                "reply": "hm",
                "remember": [
                    {"kind": "nonsense", "content": "Their flatmate is Sam", "importance": 9},
                    {"content": ""},
                    "not-a-dict",
                ],
            }
        )
    )
    assert len(parsed.memories) == 1
    candidate = parsed.memories[0]
    assert candidate.kind == "fact"
    assert candidate.importance == 1.0


def test_clean_reply_strips_artefacts():
    assert clean_reply('```\nLeyla: "hello there"\n```') == "hello there"
    assert clean_reply("a\n\n\n\nb") == "a\n\nb"


def test_parse_candidates_handles_bad_supersede_ids():
    candidates = parse_candidates(
        json.dumps(
            {
                "memories": [
                    {"content": "Uses a Pixel now", "supersedes_id": 99},
                ]
            }
        ),
        valid_ids={1, 2},
    )
    assert candidates[0].supersedes_id is None


@pytest.mark.asyncio
async def test_engine_stores_memory_from_envelope(settings, db):
    reply = {
        "reply": "wait, Miso is such a good cat name.",
        "mood": {"name": "happy", "intensity": 0.6, "why": "cat talk"},
        "voice": {"send": False, "energy": "medium"},
        "remember": [
            {"kind": "person", "content": "Their cat is called Miso", "importance": 0.8}
        ],
    }
    provider = ScriptedProvider(json.dumps(reply))
    engine, store, conversations = _engine(settings, db, provider)

    result = await engine.respond("42", "my cat Miso just knocked a glass off the table", display_name="Alex")

    assert result.reply.startswith("wait,")
    assert result.mood.mood is Mood.HAPPY
    assert result.saved_memories and "Miso" in result.saved_memories[0].content
    assert await store.count("42") == 1

    history = await conversations.recent("42")
    assert [m["role"] for m in history] == ["user", "assistant"]
    # The system prompt carried the persona and the user message was included.
    request = provider.requests[0]
    assert request.json_mode is True
    assert "You are Leyla" in request.messages[0].content
    assert request.messages[-1].content.startswith("my cat Miso")


@pytest.mark.asyncio
async def test_engine_falls_back_to_extraction_on_plain_text(settings, db):
    plain = "honestly that sounds like a terrible idea and I respect it"
    extraction = {
        "memories": [
            {"kind": "plan", "content": "Planning to quit their job in spring", "importance": 0.9}
        ]
    }
    provider = ScriptedProvider(plain, json.dumps(extraction))
    engine, store, _ = _engine(settings, db, provider)

    result = await engine.respond("42", "I think I'm going to quit my job in spring", display_name="Alex")

    assert result.reply == plain
    assert result.fallback_extraction is True
    assert result.saved_memories
    assert await store.count("42") == 1


@pytest.mark.asyncio
async def test_engine_deduplicates_repeated_memories(settings, db):
    envelope = json.dumps(
        {
            "reply": "noted.",
            "remember": [{"kind": "fact", "content": "They work as a nurse", "importance": 0.7}],
        }
    )
    provider = ScriptedProvider(envelope, envelope)
    engine, store, _ = _engine(settings, db, provider)

    await engine.respond("42", "I work as a nurse, night shifts mostly", display_name="Alex")
    second = await engine.respond("42", "anyway, night shift again tonight", display_name="Alex")
    assert second.saved_memories == []
    assert await store.count("42") == 1


@pytest.mark.asyncio
async def test_engine_supersedes_outdated_memory(settings, db):
    engine, store, _ = _engine(settings, db, ScriptedProvider())
    old, _ = await store.add("42", "Has an iPhone 12", kind="fact", importance=0.6)
    assert old is not None

    envelope = json.dumps(
        {
            "reply": "about time.",
            "remember": [
                {
                    "kind": "fact",
                    "content": "Uses a Pixel 9 now",
                    "importance": 0.7,
                    "supersedes_id": old.id,
                }
            ],
        }
    )
    engine.chain.providers[0] = ScriptedProvider(envelope)
    await engine.respond("42", "I finally switched to a Pixel 9 last week", display_name="Alex")

    assert await store.get("42", old.id) is None
    contents = [m.content for m in await store.list("42")]
    assert any("Pixel 9" in content for content in contents)


@pytest.mark.asyncio
async def test_engine_enforces_daily_budget(settings, db):
    settings.ai_daily_request_limit = 1
    engine, _, _ = _engine(settings, db, ScriptedProvider())
    import time

    day = time.strftime("%Y-%m-%d", time.gmtime())
    await db.increment_usage(day, "llm", 1)
    with pytest.raises(BudgetExceededError):
        await engine.respond("42", "hello there friend", display_name="Alex")


@pytest.mark.asyncio
async def test_engine_propagates_provider_errors(settings, db):
    engine, _, _ = _engine(
        settings, db, ScriptedProvider(fail_with=ProviderError("boom", provider="scripted"))
    )
    with pytest.raises(ProviderError):
        await engine.respond("42", "hello there friend", display_name="Alex")


@pytest.mark.asyncio
async def test_engine_reset_keeps_memories(settings, db):
    engine, store, conversations = _engine(
        settings, db, ScriptedProvider('{"reply": "mm"}', '{"reply": "mm"}')
    )
    await store.add("42", "Likes rainy mornings", importance=0.8)
    await engine.respond("42", "morning, it's raining again", display_name="Alex")
    removed = await engine.reset_conversation("42")
    assert removed >= 2
    assert await store.count("42") == 1
