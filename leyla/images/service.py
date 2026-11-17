"""Image service: fixed identity assets first, optional generation second."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..db import Database
from ..errors import ImageError
from ..logging_setup import get_logger
from ..mood.models import Mood
from .assets import CharacterAssets
from .providers import ImageProvider, build_image_provider

log = get_logger("leyla.images")


@dataclass(slots=True)
class ImageResult:
    path: Path
    source: str
    caption: str = ""


class ImageService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        *,
        assets: CharacterAssets | None = None,
        provider: ImageProvider | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.assets = assets or CharacterAssets(Path("assets/character"))
        self.provider = provider if provider is not None else build_image_provider(settings)

    @property
    def generation_enabled(self) -> bool:
        return self.provider is not None

    def default_portrait(self) -> Path | None:
        return self.assets.default()

    def portrait_for(self, mood: Mood | str) -> Path | None:
        return self.assets.for_mood(mood)

    async def generate(self, scene: str) -> ImageResult:
        """Generate one consistent-character image, subject to a daily cap."""
        if self.provider is None:
            raise ImageError(
                "image generation is disabled — set IMAGE_GENERATION_ENABLED=true "
                "and IMAGE_PROVIDER=pollinations (keyless) or openai"
            )
        day = time.strftime("%Y-%m-%d", time.gmtime())
        used = await self.db.get_usage(day, "images")
        if used >= self.settings.image_daily_limit:
            raise ImageError(
                f"daily image limit reached ({used}/{self.settings.image_daily_limit})"
            )

        data, extension, prompt_used = await self.provider.generate(
            scene, size=self.settings.image_size
        )
        target_dir = self.settings.data_dir / "images"
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"leyla_{int(time.time())}.{extension}"
        path = target_dir / filename
        path.write_bytes(data)
        await self.db.increment_usage(day, "images", 1)
        log.info("generated image %s (%s bytes)", filename, len(data))
        return ImageResult(path=path, source=self.provider.name, caption=prompt_used[:180])
