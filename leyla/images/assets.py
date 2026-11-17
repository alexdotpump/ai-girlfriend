"""Character asset registry.

Leyla has one visual identity. Every picture sent must come from this folder and
match a mood tag — the bot never sends a random image and calls it her.

Drop your own art in ``assets/character/`` using the same file names to replace
the procedural placeholders shipped with the repo.
"""

from __future__ import annotations

from pathlib import Path

from ..logging_setup import get_logger
from ..mood.models import Mood

log = get_logger("leyla.images.assets")

MOOD_FILES: dict[str, str] = {
    "default": "leyla_default.png",
    "happy": "leyla_happy.png",
    "playful": "leyla_playful.png",
    "excited": "leyla_excited.png",
    "relaxed": "leyla_relaxed.png",
    "curious": "leyla_curious.png",
    "sarcastic": "leyla_sarcastic.png",
    "sleepy": "leyla_sleepy.png",
    "shy": "leyla_shy.png",
    "annoyed": "leyla_annoyed.png",
    "disappointed": "leyla_disappointed.png",
    "serious": "leyla_serious.png",
}

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class CharacterAssets:
    def __init__(self, root: Path | str = Path("assets/character")) -> None:
        self.root = Path(root)

    def _find(self, stem: str) -> Path | None:
        for suffix in SUPPORTED_SUFFIXES:
            candidate = self.root / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
        return None

    def default(self) -> Path | None:
        return self._find("leyla_default") or self._find("leyla")

    def for_mood(self, mood: Mood | str) -> Path | None:
        key = mood.value if isinstance(mood, Mood) else str(mood).lower()
        specific = MOOD_FILES.get(key)
        if specific:
            found = self._find(Path(specific).stem)
            if found:
                return found
        return self.default()

    def available(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            path.name for path in self.root.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )

    def missing_identity(self) -> bool:
        return self.default() is None
