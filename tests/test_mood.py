"""Mood engine: transitions, hysteresis, decay and inference."""

from __future__ import annotations

import pytest

from leyla.mood.engine import MoodEngine, MoodSuggestion, infer_mood_from_text
from leyla.mood.models import Mood, spec_for, ttl_for


@pytest.mark.asyncio
async def test_baseline_mood(db):
    engine = MoodEngine(db)
    state = await engine.get("42")
    assert state.mood is Mood.RELAXED
    assert 0 < state.intensity <= 1


@pytest.mark.asyncio
async def test_mood_switches_after_dwell(db):
    engine = MoodEngine(db)
    first = await engine.apply("42", MoodSuggestion(Mood.PLAYFUL, 0.8, "joking around"), now=1000)
    assert first.mood is Mood.PLAYFUL

    blocked = await engine.apply("42", MoodSuggestion(Mood.ANNOYED, 0.45), now=1005)
    assert blocked.mood is Mood.PLAYFUL, "a weak shift during the dwell window must not apply"

    switched = await engine.apply("42", MoodSuggestion(Mood.SERIOUS, 0.9, "bad news"), now=1050)
    assert switched.mood is Mood.SERIOUS
    assert switched.changes == first.changes + 1


@pytest.mark.asyncio
async def test_strong_signal_overrides_dwell(db):
    engine = MoodEngine(db)
    await engine.apply("42", MoodSuggestion(Mood.RELAXED, 0.4), now=2000)
    strong = await engine.apply("42", MoodSuggestion(Mood.EXCITED, 0.95), now=2005)
    assert strong.mood is Mood.EXCITED


@pytest.mark.asyncio
async def test_same_mood_blends_intensity(db):
    engine = MoodEngine(db)
    await engine.apply("42", MoodSuggestion(Mood.HAPPY, 0.4), now=3000)
    blended = await engine.apply("42", MoodSuggestion(Mood.HAPPY, 0.6), now=3010)
    assert blended.mood is Mood.HAPPY
    assert blended.intensity >= 0.4


@pytest.mark.asyncio
async def test_mood_decays_to_baseline(db):
    engine = MoodEngine(db)
    await engine.apply("42", MoodSuggestion(Mood.ANNOYED, 0.7), now=4000)
    later = await engine.get("42", now=4000 + 5000)
    assert later.mood is Mood.RELAXED


@pytest.mark.asyncio
async def test_weak_signal_is_ignored(db):
    engine = MoodEngine(db)
    before = await engine.get("42")
    after = await engine.apply("42", MoodSuggestion(Mood.SHY, 0.1), now=5000)
    assert after.mood is before.mood


def test_inference_from_text():
    playful = infer_mood_from_text("lmao that's hilarious 😂")
    assert playful is not None and playful.mood is Mood.PLAYFUL

    serious = infer_mood_from_text("my grandfather died last night")
    assert serious is not None and serious.mood is Mood.SERIOUS

    sleepy = infer_mood_from_text("it's 3am and I can't sleep")
    assert sleepy is not None and sleepy.mood is Mood.SLEEPY

    assert infer_mood_from_text("ok") is None


def test_ttl_bounds():
    assert 120 <= ttl_for(Mood.SERIOUS, 0.1) <= 900
    assert 120 <= ttl_for(Mood.ANNOYED, 1.0) <= 900
    # Stronger moods last longer.
    assert ttl_for(Mood.HAPPY, 0.9) > ttl_for(Mood.HAPPY, 0.2)


def test_spec_lookup_is_forgiving():
    assert spec_for("playful").label == "playful"
    assert spec_for("nonsense").label == "relaxed"
