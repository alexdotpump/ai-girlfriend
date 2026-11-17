"""ElevenLabs provider — the expressive path.

Why this one: Eleven v3/v4 accept bracketed *audio tags* (``[laughs]``,
``[sighs]``, ``[exhales]``, ``[whispers]``, ``[clears throat]``) and render them
as actual nonverbal vocalisations in the same take, with a stable voice identity.
That is the only verified way to get natural laughter/breathing inside a single
performance rather than pasting stock audio clips together.

Free plan: 10k credits/month (≈10 minutes of audio), no commercial licence.
Private personal use is fine; see docs/COSTS.md.
"""

from __future__ import annotations

import re

from ...errors import ProviderError, VoiceError
from ..base import SpeechAudio, VoiceProvider

TAG_PATTERN = re.compile(r"\[[^\[\]]{1,40}\]")
TAGGED_MODELS = {"eleven_v3", "eleven_v4", "eleven_v4_turbo"}

API_BASE = "https://api.elevenlabs.io/v1"


class ElevenLabsProvider(VoiceProvider):
    name = "elevenlabs"
    supports_audio_tags = True

    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str,
        model: str = "eleven_v4",
        fallback_models: tuple[str, ...] = ("eleven_v3", "eleven_multilingual_v2"),
        stability: float = 0.45,
        similarity_boost: float = 0.8,
        style: float = 0.35,
        speaker_boost: bool = True,
        timeout: float = 60.0,
        max_retries: int = 1,
    ) -> None:
        if not api_key:
            raise VoiceError("ELEVENLABS_API_KEY is missing")
        if not voice_id:
            raise VoiceError("ELEVENLABS_VOICE_ID is missing")
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.fallback_models = tuple(fallback_models)
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.style = style
        self.speaker_boost = speaker_boost
        self.timeout = timeout
        self.max_retries = max_retries

    @property
    def current_supports_tags(self) -> bool:
        return self.model in TAGGED_MODELS

    async def synthesize(self, text: str, *, delivery: str = "") -> SpeechAudio:
        from ...ai.http import HttpJsonClient

        models = [self.model, *[m for m in self.fallback_models if m != self.model]]
        last_error: Exception | None = None
        for model in models:
            body = text if model in TAGGED_MODELS else TAG_PATTERN.sub("", text)
            body = re.sub(r"\s{2,}", " ", body).strip()
            if not body:
                raise VoiceError("nothing to speak after cleaning the text")

            payload = {
                "text": body,
                "model_id": model,
                "voice_settings": {
                    "stability": self.stability,
                    "similarity_boost": self.similarity_boost,
                    "style": self.style,
                    "use_speaker_boost": self.speaker_boost,
                },
            }
            client = HttpJsonClient("elevenlabs", max_retries=self.max_retries, timeout=self.timeout)
            try:
                data, headers = await client.request(
                    "POST",
                    f"{API_BASE}/text-to-speech/{self.voice_id}",
                    headers={
                        "xi-api-key": self.api_key,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg",
                    },
                    json_body=payload,
                    timeout=self.timeout,
                    expect_json=False,
                )
            except ProviderError as exc:
                last_error = exc
                # A model that is unavailable on the plan must not kill the note.
                if exc.status in {400, 402, 403, 404, 422}:
                    continue
                raise

            if not isinstance(data, (bytes, bytearray)) or len(data) < 512:
                last_error = ProviderError("ElevenLabs returned no audio", provider=self.name)
                continue
            mime = "audio/mpeg"
            if "content-type" in {k.lower() for k in headers}:
                mime = next(
                    (v for k, v in headers.items() if k.lower() == "content-type"), mime
                ).split(";")[0]
            extension = "mp3" if "mpeg" in mime or "mp3" in mime else "ogg"
            return SpeechAudio(
                data=bytes(data),
                mime=mime,
                extension=extension,
                provider=self.name,
                model=model,
                characters=len(body),
            )

        raise VoiceError(f"ElevenLabs synthesis failed: {last_error}")
