"""Microsoft Edge TTS via the optional ``edge-tts`` package.

Free, no API key, decent neural voices and SSML-level prosody control — but it
cannot produce laughter/breathing. Kept as a zero-cost fallback for people who
refuse to pay for voice; expression is limited to pacing and punctuation.

The package is optional so the default install stays tiny (KataBump free plan).
"""

from __future__ import annotations

import re

from ...errors import VoiceError
from ..base import SpeechAudio, VoiceProvider

TAG_PATTERN = re.compile(r"\[[^\[\]]{1,40}\]")


class EdgeTTSProvider(VoiceProvider):
    name = "edge"
    supports_audio_tags = False
    free = True

    def __init__(self, *, voice: str = "en-US-AvaMultilingualNeural") -> None:
        self.voice = voice

    async def synthesize(self, text: str, *, delivery: str = "") -> SpeechAudio:
        try:
            import edge_tts  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise VoiceError(
                "edge-tts is not installed. Run 'pip install edge-tts' or choose "
                "VOICE_PROVIDER=elevenlabs/openai."
            ) from exc

        clean = TAG_PATTERN.sub("", text)
        clean = re.sub(r"\s{2,}", " ", clean).strip()
        if not clean:
            raise VoiceError("nothing to speak after cleaning the text")

        communicate = edge_tts.Communicate(clean, self.voice, rate="+2%", pitch="+0Hz")
        buffer = bytearray()
        try:
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    buffer.extend(chunk["data"])
        except Exception as exc:  # noqa: BLE001 - third-party error surface
            raise VoiceError(f"edge-tts failed: {exc}") from exc

        if len(buffer) < 512:
            raise VoiceError("edge-tts returned no audio")
        return SpeechAudio(
            data=bytes(buffer),
            mime="audio/mpeg",
            extension="mp3",
            provider=self.name,
            model=self.voice,
            characters=len(clean),
        )
