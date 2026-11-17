"""Optional image-generation providers.

Off by default. Pollinations is keyless (still rate-limited); OpenAI images are
paid. Both are wrapped behind the same interface so a new provider is a small
addition.

Safety: prompts are constrained to a consistent, tasteful character description,
and a blocklist refuses sexual/explicit or real-person requests outright.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from urllib.parse import quote

from ..errors import ImageError
from ..logging_setup import get_logger

log = get_logger("leyla.images.providers")

CHARACTER_PROMPT = (
    "portrait illustration of Leyla, a fictional young woman in her mid-twenties, "
    "soft warm lighting, dark wavy hair, expressive eyes, casual clothes, "
    "friendly relaxed expression, digital painting, cohesive character design, "
    "same face as previous portraits"
)

STYLE_SUFFIX = (
    "tasteful, fully clothed, wholesome, safe for work, high quality, "
    "consistent character design, no text, no watermark"
)

BLOCKED_TERMS = {
    "nude", "naked", "nsfw", "explicit", "sex", "sexual", "porn", "lingerie",
    "underwear", "bikini", "fetish", "cleavage", "upskirt", "loli", "child",
    "minor", "teen", "gore", "blood", "violence", "torture", "corpse",
    "celebrity", "real person", "deepfake",
}


def validate_prompt(prompt: str) -> str:
    cleaned = re.sub(r"\s+", " ", (prompt or "").strip())[:300]
    if not cleaned:
        raise ImageError("empty image prompt")
    lowered = cleaned.lower()
    for term in BLOCKED_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            raise ImageError("that request is off-limits")
    return cleaned


def build_prompt(scene: str) -> str:
    scene = validate_prompt(scene)
    return f"{CHARACTER_PROMPT}, {scene}, {STYLE_SUFFIX}"


class ImageProvider(ABC):
    name = "none"

    @abstractmethod
    async def generate(self, prompt: str, *, size: str = "1024x1024") -> tuple[bytes, str, str]:
        """Return ``(image_bytes, extension, prompt_used)``."""


class PollinationsProvider(ImageProvider):
    """Keyless image generation (free, rate-limited, no uptime guarantee)."""

    name = "pollinations"

    def __init__(self, *, timeout: float = 90.0, model: str = "flux") -> None:
        self.timeout = timeout
        self.model = model

    async def generate(self, prompt: str, *, size: str = "1024x1024") -> tuple[bytes, str, str]:
        from ..ai.http import get_session

        width, _, height = size.partition("x")
        full_prompt = build_prompt(prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{quote(full_prompt)}"
            f"?width={int(width or 1024)}&height={int(height or 1024)}"
            f"&model={self.model}&nologo=true&safe=true"
        )
        session = await get_session()
        try:
            async with session.get(url, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ImageError(f"image service returned {response.status}")
                data = await response.read()
        except ImageError:
            raise
        except Exception as exc:  # noqa: BLE001 - network surface
            raise ImageError(f"image generation failed: {exc}") from exc
        if len(data) < 1024:
            raise ImageError("image service returned an empty image")
        return data, "jpg", full_prompt


class OpenAIImageProvider(ImageProvider):
    """Paid option; disabled unless a key is configured and generation is enabled."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-image-1",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise ImageError("OPENAI_API_KEY is required for IMAGE_PROVIDER=openai")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def generate(self, prompt: str, *, size: str = "1024x1024") -> tuple[bytes, str, str]:
        import base64

        from ..ai.http import HttpJsonClient

        full_prompt = build_prompt(prompt)
        client = HttpJsonClient("openai-images", max_retries=1, timeout=self.timeout)
        try:
            data, _ = await client.request(
                "POST",
                f"{self.base_url}/images/generations",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json_body={"model": self.model, "prompt": full_prompt, "size": size, "n": 1},
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - network surface
            raise ImageError(f"image generation failed: {exc}") from exc

        items = data.get("data") or []
        if not items:
            raise ImageError("image service returned no image")
        encoded = items[0].get("b64_json")
        if encoded:
            return base64.b64decode(encoded), "png", full_prompt
        url = items[0].get("url")
        if url:
            from ..ai.http import get_session

            session = await get_session()
            async with session.get(url, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ImageError("could not download the generated image")
                return await response.read(), "png", full_prompt
        raise ImageError("image service returned no usable image")


def build_image_provider(settings) -> ImageProvider | None:
    """Return a provider only when generation is enabled and configured."""
    if not settings.image_generation_enabled or settings.image_provider == "none":
        return None
    try:
        if settings.image_provider == "pollinations":
            return PollinationsProvider()
        if settings.image_provider == "openai":
            return OpenAIImageProvider(settings.openai_api_key or settings.ai_api_key)
    except ImageError as exc:
        log.error("image provider unavailable: %s", exc)
        return None
    return None
