"""Voice provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class SpeechAudio:
    data: bytes
    mime: str
    extension: str
    provider: str
    model: str
    characters: int = 0


class VoiceProvider(ABC):
    """A text-to-speech backend.

    ``supports_audio_tags`` means the provider understands bracketed performance
    cues such as ``[laughs]`` or ``[sighs]``; other providers receive clean text.
    """

    name: str = "unknown"
    supports_audio_tags: bool = False
    #: How bracketed cues must be written for this engine. ``elevenlabs`` takes
    #: the canonical ``[laughs]`` form; ``orpheus`` uses ``<laugh>`` tokens.
    tag_style: str = "elevenlabs"
    #: Hard input cap in characters, 0 for "use the configured limit".
    max_input_chars: int = 0
    #: Set when the provider can be used without an API key.
    free: bool = False

    @abstractmethod
    async def synthesize(self, text: str, *, delivery: str = "") -> SpeechAudio:
        """Render ``text`` to audio bytes."""

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


class NullVoiceProvider(VoiceProvider):
    """Placeholder used when voice is switched off or unconfigured."""

    name = "none"
    supports_audio_tags = False
    max_input_chars = 0

    async def synthesize(self, text: str, *, delivery: str = "") -> SpeechAudio:
        from ..errors import VoiceError

        raise VoiceError("voice provider is disabled")
