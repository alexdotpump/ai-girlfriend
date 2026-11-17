"""Groq Orpheus text-to-speech — the current default.

Orpheus (``canopylabs/orpheus-v1-english``) is expressive in the way voice notes
need: besides bracketed *directions* like ``[cheerful]`` it renders Canopy Labs'
nonverbal tokens — ``<laugh>``, ``<chuckle>``, ``<sigh>``, ``<gasp>`` — as real
vocalisations inside the same take. Tags are never read out loud (verified: the
transcript of ``<laugh> Wait, you actually did that?`` is just the sentence).

Two hard limits come from the API and shape the whole voice path:
* input is capped at **200 characters** (tags included);
* only ``wav`` is supported, so ffmpeg transcodes to Ogg/Opus for Discord.
"""

from __future__ import annotations

import re

from ...errors import ProviderError, VoiceError
from ..base import SpeechAudio, VoiceProvider

API_URL = "https://api.groq.com/openai/v1/audio/speech"
MAX_INPUT_CHARS = 200

_TAG = re.compile(r"<[^<>\n]{1,24}>|\[[^\[\]\n]{1,24}\]")


def _trim_to_words(text: str, limit: int) -> str:
    """Cut on a word or sentence boundary, never mid-word."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for mark in (". ", "! ", "? ", "… ", ", ", " "):
        cut = window.rfind(mark)
        if cut > limit * 0.4:
            return window[: cut + len(mark)].strip()
    return window.strip()


class GroqTTSProvider(VoiceProvider):
    name = "groq"
    supports_audio_tags = True
    tag_style = "orpheus"
    max_input_chars = MAX_INPUT_CHARS

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "canopylabs/orpheus-v1-english",
        voice: str = "autumn",
        base_url: str = API_URL,
        timeout: float = 45.0,
        max_retries: int = 1,
    ) -> None:
        if not api_key:
            raise VoiceError("GROQ_API_KEY is missing")
        if not voice:
            raise VoiceError("GROQ_TTS_VOICE is missing")
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.url = base_url
        self.timeout = timeout
        self.max_retries = max_retries

    async def synthesize(self, text: str, *, delivery: str = "") -> SpeechAudio:
        from ...ai.http import HttpJsonClient

        body = _trim_to_words((text or "").strip(), MAX_INPUT_CHARS)
        if not body:
            raise VoiceError("nothing to speak")

        for attempt in (1, 2):
            payload = {
                "model": self.model,
                "voice": self.voice,
                "input": body,
                "response_format": "wav",
            }
            client = HttpJsonClient("groq-tts", max_retries=self.max_retries, timeout=self.timeout)
            try:
                data, headers = await client.request(
                    "POST",
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Accept": "audio/wav",
                    },
                    json_body=payload,
                    timeout=self.timeout,
                    expect_json=False,
                )
            except ProviderError as exc:
                # A rejected cue must not cost the whole note: retry once clean.
                if attempt == 1 and exc.status in {400, 422} and _TAG.search(body):
                    body = _trim_to_words(_TAG.sub("", body), MAX_INPUT_CHARS)
                    body = re.sub(r"\s{2,}", " ", body).strip()
                    if body:
                        continue
                raise VoiceError(f"Groq TTS failed: {exc}") from exc

            if not isinstance(data, (bytes, bytearray)) or len(data) < 512:
                raise VoiceError("Groq TTS returned no audio")
            mime = "audio/wav"
            for key, value in headers.items():
                if key.lower() == "content-type":
                    mime = str(value).split(";")[0]
                    break
            extension = "wav" if "wav" in mime else "mp3"
            return SpeechAudio(
                data=bytes(data),
                mime=mime,
                extension=extension,
                provider=self.name,
                model=self.model,
                characters=len(body),
            )

        raise VoiceError("Groq TTS failed: no usable text after cleaning")
