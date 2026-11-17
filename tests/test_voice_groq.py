"""Groq Orpheus voice provider: request shape, cue dialect, hard 200-char cap."""

from __future__ import annotations

import pytest

from leyla.config import Settings
from leyla.errors import ProviderError, VoiceError
from leyla.mood.models import Mood, MoodState
from leyla.voice.director import VoiceDirector, compose_spoken_text, render_tag
from leyla.voice.providers.groq_tts import GroqTTSProvider


def mood_state(mood: Mood = Mood.PLAYFUL) -> MoodState:
    return MoodState(mood=mood, intensity=0.9, reason="test")


def test_orpheus_renders_real_laughter_tokens():
    assert render_tag("[laughs]", "orpheus") == "<laugh>"
    assert render_tag("[sighs]", "orpheus") == "<sigh>"
    assert render_tag("[nervous laugh]", "orpheus") == "<chuckle>"
    # Cues Orpheus has no token for are dropped, never spoken out loud.
    assert render_tag("[whispers]", "orpheus") == ""

    text = compose_spoken_text("you actually did that", ("[laughs]",), style="orpheus")
    assert text.startswith("<laugh>")
    assert "[laughs]" not in text


@pytest.mark.asyncio
async def test_plan_keeps_orpheus_text_within_the_api_cap(db):
    director = VoiceDirector(db)
    reply = (
        "okay so I was going to be calm about this and then I remembered the "
        "entire thing you said last night and honestly it is still unreasonable."
    )
    plan = await director.plan(
        scope_id="42",
        reply=reply,
        mood=mood_state(),
        mode="always",
        request="explicit",
        energy="high",
        tag_style="orpheus",
        max_chars=200,
    )
    assert plan.should_send
    assert len(plan.text) <= 200
    assert "[" not in plan.text


@pytest.mark.asyncio
async def test_request_payload_and_wav_output(monkeypatch):
    provider = GroqTTSProvider("test-key", voice="autumn")
    captured: dict = {}

    async def fake_request(self, method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return b"RIFF" + b"0" * 2048, {"content-type": "audio/wav"}

    monkeypatch.setattr(
        "leyla.ai.http.HttpJsonClient.request", fake_request
    )
    speech = await provider.synthesize("<laugh> you actually did that?")

    assert captured["url"].endswith("/openai/v1/audio/speech")
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json_body"] == {
        "model": "canopylabs/orpheus-v1-english",
        "voice": "autumn",
        "input": "<laugh> you actually did that?",
        "response_format": "wav",
    }
    assert speech.extension == "wav"
    assert speech.mime == "audio/wav"
    assert speech.provider == "groq"


@pytest.mark.asyncio
async def test_long_text_is_trimmed_before_the_api_call(monkeypatch):
    provider = GroqTTSProvider("test-key")
    captured: dict = {}

    async def fake_request(self, method, url, **kwargs):
        captured.update(kwargs)
        return b"RIFF" + b"0" * 2048, {"content-type": "audio/wav"}

    monkeypatch.setattr("leyla.ai.http.HttpJsonClient.request", fake_request)
    await provider.synthesize("word " * 200)

    assert len(captured["json_body"]["input"]) <= 200


@pytest.mark.asyncio
async def test_rejected_cue_is_retried_without_tags(monkeypatch):
    provider = GroqTTSProvider("test-key")
    bodies: list[str] = []

    async def fake_request(self, method, url, **kwargs):
        bodies.append(kwargs["json_body"]["input"])
        if len(bodies) == 1:
            raise ProviderError("invalid input", provider="groq", status=400)
        return b"RIFF" + b"0" * 2048, {"content-type": "audio/wav"}

    monkeypatch.setattr("leyla.ai.http.HttpJsonClient.request", fake_request)
    speech = await provider.synthesize("<laugh> that was ridiculous")
    assert bodies[0].startswith("<laugh>")
    assert not bodies[1].startswith("<laugh>")
    assert speech.characters == len(bodies[1])


def test_missing_key_fails_loudly():
    with pytest.raises(VoiceError):
        GroqTTSProvider("")


def test_groq_voice_requires_a_groq_key():
    settings = Settings.load(
        {
            "DISCORD_TOKEN": "abc",
            "OWNER_DISCORD_ID": "42",
            "AI_PROVIDER": "echo",
            "VOICE_PROVIDER": "groq",
            "VOICE_MODE": "occasional",
        }
    )
    assert any("GROQ_API_KEY" in problem for problem in settings.problems())
    assert settings.tts_api_key() == ""
