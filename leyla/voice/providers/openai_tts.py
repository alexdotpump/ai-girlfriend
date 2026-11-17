"""OpenAI text-to-speech (paid, optional fallback).

No inline audio tags: expression comes from the natural-language ``instructions``
field instead, so the director strips tags before calling it. Cheaper per note
than ElevenLabs but less expressive.
"""

from __future__ import annotations

from ...errors import ProviderError, VoiceError
from ..base import SpeechAudio, VoiceProvider


class OpenAITTSProvider(VoiceProvider):
    name = "openai"
    supports_audio_tags = False

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-4o-mini-tts",
        voice: str = "sage",
        instructions: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        max_retries: int = 1,
    ) -> None:
        if not api_key:
            raise VoiceError("OPENAI_API_KEY is missing")
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.instructions = instructions
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    async def synthesize(self, text: str, *, delivery: str = "") -> SpeechAudio:
        from ...ai.http import HttpJsonClient

        instructions = " ".join(part for part in (self.instructions, delivery) if part).strip()
        payload = {
            "model": self.model,
            "voice": self.voice,
            "input": text[:2000],
            "response_format": "mp3",
        }
        if instructions:
            payload["instructions"] = instructions[:900]

        client = HttpJsonClient("openai-tts", max_retries=self.max_retries, timeout=self.timeout)
        try:
            data, _ = await client.request(
                "POST",
                f"{self.base_url}/audio/speech",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json_body=payload,
                timeout=self.timeout,
                expect_json=False,
            )
        except ProviderError as exc:
            raise VoiceError(f"OpenAI TTS failed: {exc}") from exc

        if not isinstance(data, (bytes, bytearray)) or len(data) < 512:
            raise VoiceError("OpenAI TTS returned no audio")
        return SpeechAudio(
            data=bytes(data),
            mime="audio/mpeg",
            extension="mp3",
            provider=self.name,
            model=self.model,
            characters=len(text),
        )
