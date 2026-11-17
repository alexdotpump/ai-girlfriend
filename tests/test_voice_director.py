"""Voice direction: gating rules, speech cleaning, nonverbal tag placement."""

from __future__ import annotations

import base64
import random

import pytest

from leyla.mood.models import Mood, MoodState, spec_for
from leyla.voice.director import (
    SUPPORTED_TAGS,
    VoiceDirector,
    clean_for_speech,
    compose_spoken_text,
)


def mood_state(mood: Mood = Mood.PLAYFUL, intensity: float = 0.8) -> MoodState:
    return MoodState(mood=mood, intensity=intensity, reason="test")


@pytest.mark.asyncio
async def test_voice_mode_off_never_sends(db):
    director = VoiceDirector(db, rng=random.Random(0))
    plan = await director.plan(
        scope_id="42",
        reply="that's a whole paragraph of perfectly speakable words, honestly.",
        mood=mood_state(),
        mode="off",
        frequency=1.0,
    )
    assert plan.should_send is False
    assert "off" in plan.reason


@pytest.mark.asyncio
async def test_request_mode_only_on_explicit(db):
    director = VoiceDirector(db, rng=random.Random(0))
    auto = await director.plan(
        scope_id="42",
        reply="okay that is genuinely a ridiculous thing to say out loud.",
        mood=mood_state(),
        mode="request",
        frequency=1.0,
    )
    assert auto.should_send is False

    explicit = await director.plan(
        scope_id="42",
        reply="okay that is genuinely a ridiculous thing to say out loud.",
        mood=mood_state(),
        mode="request",
        request="explicit",
        frequency=0.0,
    )
    assert explicit.should_send is True
    assert explicit.spoken_text
    assert explicit.characters > 0


@pytest.mark.asyncio
async def test_occasional_respects_frequency_and_cooldown(db):
    director = VoiceDirector(db, rng=random.Random(1))
    reply = "come on, you know exactly what I meant by that, don't pretend."
    plan = await director.plan(
        scope_id="42", reply=reply, mood=mood_state(Mood.RELAXED, 0.5),
        mode="occasional", frequency=1.0,
    )
    assert plan.should_send is True

    await director.mark_sent("42", plan)
    blocked = await director.plan(
        scope_id="42", reply=reply, mood=mood_state(Mood.RELAXED, 0.5),
        mode="occasional", frequency=1.0,
    )
    assert blocked.should_send is False
    assert "cooldown" in blocked.reason


@pytest.mark.asyncio
async def test_zero_frequency_disables_occasional(db):
    director = VoiceDirector(db, rng=random.Random(0))
    plan = await director.plan(
        scope_id="42",
        reply="this is long enough to qualify as something worth speaking aloud.",
        mood=mood_state(Mood.EXCITED, 1.0),
        mode="occasional",
        frequency=0.0,
    )
    assert plan.should_send is False


@pytest.mark.asyncio
async def test_long_replies_and_links_are_not_spoken(db):
    director = VoiceDirector(db, max_chars=100)
    long_reply = "word " * 200
    plan = await director.plan(
        scope_id="42", reply=long_reply, mood=mood_state(), mode="always", request="explicit"
    )
    assert plan.should_send is False

    link_only = await director.plan(
        scope_id="42", reply="https://example.com/very/long/link", mood=mood_state(),
        mode="always", request="explicit",
    )
    assert link_only.should_send is False


@pytest.mark.asyncio
async def test_nonverbal_tags_are_mood_appropriate_and_limited(db):
    director = VoiceDirector(db, rng=random.Random(2))
    plan = await director.plan(
        scope_id="42",
        reply="okay so that happened, and I refuse to be normal about it.",
        mood=mood_state(Mood.PLAYFUL, 0.9),
        mode="always",
        request="explicit",
        energy="high",
    )
    assert plan.should_send
    assert len(plan.tags) <= 2
    assert set(plan.tags) <= SUPPORTED_TAGS
    for tag in plan.tags:
        assert tag in plan.text


@pytest.mark.asyncio
async def test_tags_can_be_disabled(db):
    director = VoiceDirector(db, nonverbal_enabled=False)
    plan = await director.plan(
        scope_id="42",
        reply="this would normally get a little laugh at the start of it, honestly.",
        mood=mood_state(Mood.PLAYFUL, 1.0),
        mode="always",
        request="explicit",
        supports_tags=True,
    )
    assert plan.should_send
    assert plan.tags == ()
    assert "[" not in plan.text


@pytest.mark.asyncio
async def test_provider_without_tag_support_gets_clean_text(db):
    director = VoiceDirector(db)
    plan = await director.plan(
        scope_id="42",
        reply="this one goes to a provider that cannot do audio tags at all.",
        mood=mood_state(Mood.SERIOUS, 0.7),
        mode="always",
        request="explicit",
        supports_tags=False,
    )
    assert plan.tags == ()
    assert "[" not in plan.text


@pytest.mark.asyncio
async def test_tag_cooldowns_prevent_repetition(db):
    director = VoiceDirector(db, rng=random.Random(3))
    reply = "so anyway I said the thing and it was a disaster, obviously."
    first = await director.plan(
        scope_id="42", reply=reply, mood=mood_state(Mood.PLAYFUL, 0.9),
        mode="always", request="explicit", energy="high",
    )
    assert first.tags
    await director.mark_sent("42", first)

    second = await director.plan(
        scope_id="42", reply=reply, mood=mood_state(Mood.PLAYFUL, 0.9),
        mode="always", request="explicit", energy="high",
    )
    assert not (set(second.tags) & set(first.tags))


def test_clean_for_speech_strips_markup_and_expands_slang():
    raw = "**omg** lol [link](https://x.test) 😂 idk about that, ngl"
    cleaned = clean_for_speech(raw)
    assert "**" not in cleaned
    assert "http" not in cleaned
    assert "😂" not in cleaned
    assert "oh my god" in cleaned
    assert "ha ha" in cleaned
    assert "I don't know" in cleaned
    assert cleaned.endswith(".")


def test_clean_for_speech_rejects_code_and_trims_on_boundary():
    assert clean_for_speech("here:\n```python\nprint(1)\n```") == ""
    long_text = ("this is a sentence about something. " * 40).strip()
    cleaned = clean_for_speech(long_text, max_chars=120)
    assert len(cleaned) <= 121
    assert cleaned.endswith(".")


def test_compose_spoken_text_places_cues_naturally():
    spoken = "I told you it would break. Then it broke."
    opening = compose_spoken_text(spoken, ("[laughs]",))
    assert opening.startswith("[laughs]")

    mid = compose_spoken_text(spoken, ("[exhales]",))
    assert "[exhales]" in mid
    assert not mid.startswith("[exhales]")


def test_waveform_and_emoji_only_text_is_empty():
    assert clean_for_speech("😂😂😂") == ""
    assert clean_for_speech("   ") == ""


def test_specs_expose_voice_affinity_for_every_mood():
    for mood in Mood:
        spec = spec_for(mood)
        assert 0.0 <= spec.voice_affinity <= 1.0
        assert spec.length_hint
