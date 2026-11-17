"""Configuration parsing and validation."""

from __future__ import annotations

import pytest

from leyla.config import Settings
from leyla.errors import ConfigError


BASE = {
    "DISCORD_TOKEN": "abc",
    "OWNER_DISCORD_ID": "42",
    "AI_PROVIDER": "echo",
    "AI_MODEL": "echo-local",
    "VOICE_MODE": "off",
}


def test_defaults_and_no_problems():
    settings = Settings.load(BASE)
    assert settings.owner_ids == frozenset({42})
    assert settings.ai_provider == "echo"
    assert settings.voice_mode == "off"
    assert settings.problems() == []


def test_missing_token_and_owner_are_reported():
    settings = Settings.load({"AI_PROVIDER": "echo", "VOICE_MODE": "off"})
    problems = settings.problems()
    assert any("DISCORD_TOKEN" in p for p in problems)
    assert any("OWNER_DISCORD_ID" in p for p in problems)


def test_gemini_requires_key_and_reports_it():
    settings = Settings.load(
        {
            "DISCORD_TOKEN": "abc",
            "OWNER_DISCORD_ID": "42",
            "AI_PROVIDER": "gemini",
            "AI_MODEL": "gemini-flash-latest",
            "VOICE_MODE": "off",
        }
    )
    problems = settings.problems()
    assert any("GEMINI_API_KEY" in p for p in problems)


def test_voice_provider_requires_key():
    settings = Settings.load(
        {**BASE, "VOICE_MODE": "occasional", "VOICE_PROVIDER": "elevenlabs"}
    )
    assert any("ELEVENLABS_API_KEY" in p for p in settings.problems())


def test_owner_ids_accepts_comma_and_space_lists():
    settings = Settings.load({**BASE, "OWNER_DISCORD_ID": "1,2 3;"})
    assert settings.owner_ids == frozenset({1, 2, 3})


def test_voice_mode_aliases():
    settings = Settings.load({**BASE, "VOICE_MODE": "on_request"})
    assert settings.voice_mode == "request"


@pytest.mark.parametrize(
    "overrides",
    [
        {"AI_PROVIDER": "not-a-provider"},
        {"VOICE_MODE": "sometimes"},
        {"VOICE_PROVIDER": "whisperer"},
        {"IMAGE_PROVIDER": "midjourney"},
        {"VOICE_FREQUENCY": "3.0"},
        {"AI_MAX_OUTPUT_TOKENS": "10"},
        {"AI_TEMPERATURE": "abc"},
        {"OWNER_DISCORD_ID": "42,not-an-id"},
        {"MEMORY_EXTRACTION_ENABLED": "maybe"},
    ],
)
def test_invalid_values_raise(overrides):
    with pytest.raises(ConfigError):
        Settings.load({**BASE, **overrides})


def test_custom_provider_requires_base_url():
    with pytest.raises(ConfigError):
        Settings.load({**BASE, "AI_PROVIDER": "custom", "AI_MODEL": "x"})


def test_primary_key_is_not_shared_with_fallbacks():
    settings = Settings.load(
        {
            "DISCORD_TOKEN": "abc",
            "OWNER_DISCORD_ID": "42",
            "AI_PROVIDER": "gemini",
            "AI_MODEL": "gemini-flash-latest",
            "AI_API_KEY": "generic-gemini-key",
            "VOICE_MODE": "off",
        }
    )
    assert settings.ai_key_for("gemini") == "generic-gemini-key"
    # A generic key must never be sent to a different vendor.
    assert settings.ai_key_for("groq") == ""


def test_discord_ids_parse_from_env_sets():
    settings = Settings.load({**BASE, "OWNER_DISCORD_IDS": "7", "ALLOWED_CHANNEL_IDS": "10, 11"})
    assert settings.owner_ids == frozenset({7, 42})
    assert settings.allowed_channel_ids == frozenset({10, 11})


def test_warnings_mention_global_sync_and_image_state():
    settings = Settings.load({**BASE, "DISCORD_GUILD_ID": "", "AI_PROVIDER": "echo"})
    warnings = " ".join(settings.warnings())
    assert "DISCORD_GUILD_ID" in warnings
    assert "image generation is disabled" in warnings
