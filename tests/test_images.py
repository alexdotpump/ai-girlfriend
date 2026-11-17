"""Character identity assets and optional generation."""

from __future__ import annotations

import pytest

from leyla.errors import ImageError
from leyla.images.assets import CharacterAssets
from leyla.images.providers import build_image_provider, build_prompt, validate_prompt
from leyla.images.service import ImageService
from leyla.mood.models import Mood


def test_asset_lookup_falls_back_to_default(tmp_path):
    assets = CharacterAssets(tmp_path)
    assert assets.default() is None
    assert assets.missing_identity() is True

    (tmp_path / "leyla_default.png").write_bytes(b"png")
    assert assets.default() is not None
    assert assets.for_mood(Mood.SLEEPY) == assets.default()

    sleepy = tmp_path / "leyla_sleepy.png"
    sleepy.write_bytes(b"png")
    assert assets.for_mood(Mood.SLEEPY) == sleepy
    assert assets.missing_identity() is False
    assert "leyla_sleepy.png" in assets.available()


def test_prompt_safety_blocklist():
    assert "coffee" in validate_prompt("  drinking coffee by a window  ")
    for bad in ("nude portrait", "explicit scene", "child", "a real person"):
        with pytest.raises(ImageError):
            validate_prompt(bad)
    with pytest.raises(ImageError):
        validate_prompt("   ")


def test_build_prompt_keeps_character_identity():
    prompt = build_prompt("walking in the rain")
    assert "Leyla" in prompt
    assert "walking in the rain" in prompt
    assert "safe for work" in prompt


def test_generation_disabled_by_default(settings, db):
    assert build_image_provider(settings) is None
    service = ImageService(settings, db, assets=CharacterAssets(settings.data_dir / "none"))
    assert service.generation_enabled is False


@pytest.mark.asyncio
async def test_generation_disabled_raises(settings, db):
    service = ImageService(settings, db, assets=CharacterAssets(settings.data_dir / "none"))
    with pytest.raises(ImageError):
        await service.generate("reading on a sofa")


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, prompt: str, *, size: str = "1024x1024"):
        self.calls += 1
        return b"\x89PNG\r\n\x1a\n" + b"0" * 2048, "png", prompt


@pytest.mark.asyncio
async def test_generation_respects_daily_limit(settings, db, tmp_path):
    settings.image_daily_limit = 1
    provider = FakeProvider()
    service = ImageService(
        settings, db, assets=CharacterAssets(tmp_path / "assets"), provider=provider
    )
    result = await service.generate("sitting on a rooftop at dusk")
    assert result.path.exists()
    assert result.path.stat().st_size > 1000

    with pytest.raises(ImageError) as excinfo:
        await service.generate("another one")
    assert "limit" in str(excinfo.value)
    assert provider.calls == 1
