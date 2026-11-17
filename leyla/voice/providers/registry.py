"""Voice provider factory."""

from __future__ import annotations

from ...config import Settings
from ...errors import VoiceError
from ...logging_setup import get_logger
from ..base import NullVoiceProvider, VoiceProvider
from .edge import EdgeTTSProvider
from .elevenlabs import ElevenLabsProvider
from .openai_tts import OpenAITTSProvider

log = get_logger("leyla.voice.registry")


def build_voice_provider(settings: Settings) -> VoiceProvider:
    """Build the configured provider, or a null provider when voice is off."""
    if settings.voice_mode == "off" or settings.voice_provider == "none":
        return NullVoiceProvider()

    try:
        if settings.voice_provider == "elevenlabs":
            return ElevenLabsProvider(
                settings.elevenlabs_api_key,
                voice_id=settings.elevenlabs_voice_id,
                model=settings.elevenlabs_model,
                fallback_models=settings.elevenlabs_model_fallbacks,
                stability=settings.elevenlabs_stability,
                similarity_boost=settings.elevenlabs_similarity,
                style=settings.elevenlabs_style,
                speaker_boost=settings.elevenlabs_speaker_boost,
            )
        if settings.voice_provider == "openai":
            return OpenAITTSProvider(
                settings.openai_api_key or settings.ai_api_key,
                model=settings.openai_tts_model,
                voice=settings.openai_tts_voice,
                instructions=settings.openai_tts_instructions,
            )
        if settings.voice_provider == "edge":
            return EdgeTTSProvider(voice=settings.edge_tts_voice)
    except VoiceError as exc:
        log.error("voice provider unavailable: %s", exc)
        return NullVoiceProvider()

    log.warning("unknown voice provider %s, voice disabled", settings.voice_provider)
    return NullVoiceProvider()
