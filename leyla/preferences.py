"""Per-user preferences (voice behaviour, images)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .config import VOICE_MODES, Settings
from .db import Database


@dataclass(slots=True)
class UserPreferences:
    voice_mode: str
    voice_frequency: float
    images_enabled: bool


class PreferenceStore:
    """SQLite-backed preferences that survive restarts and redeploys."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def get(self, scope_id: str, user_id: int | str) -> UserPreferences:
        row = await self.db.fetchone(
            "SELECT * FROM preferences WHERE scope_id = ? AND user_id = ?",
            (scope_id, str(user_id)),
        )
        if row is None:
            return UserPreferences(
                voice_mode=self.settings.voice_mode,
                voice_frequency=self.settings.voice_frequency,
                images_enabled=True,
            )
        return UserPreferences(
            voice_mode=row["voice_mode"] or self.settings.voice_mode,
            voice_frequency=(
                float(row["voice_frequency"])
                if row["voice_frequency"] is not None
                else self.settings.voice_frequency
            ),
            images_enabled=(
                bool(row["images_enabled"]) if row["images_enabled"] is not None else True
            ),
        )

    async def set_voice_mode(self, scope_id: str, user_id: int | str, mode: str) -> UserPreferences:
        mode = mode.strip().lower()
        if mode not in VOICE_MODES:
            raise ValueError(f"voice mode must be one of {', '.join(VOICE_MODES)}")
        await self._upsert(scope_id, user_id, voice_mode=mode)
        return await self.get(scope_id, user_id)

    async def set_voice_frequency(
        self, scope_id: str, user_id: int | str, frequency: float
    ) -> UserPreferences:
        frequency = max(0.0, min(1.0, float(frequency)))
        await self._upsert(scope_id, user_id, voice_frequency=frequency)
        return await self.get(scope_id, user_id)

    async def set_images_enabled(
        self, scope_id: str, user_id: int | str, enabled: bool
    ) -> UserPreferences:
        await self._upsert(scope_id, user_id, images_enabled=1 if enabled else 0)
        return await self.get(scope_id, user_id)

    async def _upsert(
        self,
        scope_id: str,
        user_id: int | str,
        *,
        voice_mode: str | None = None,
        voice_frequency: float | None = None,
        images_enabled: int | None = None,
    ) -> None:
        now = time.time()
        await self.db.execute(
            "INSERT INTO preferences(scope_id, user_id, voice_mode, voice_frequency, images_enabled, updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(scope_id, user_id) DO UPDATE SET "
            "voice_mode = COALESCE(excluded.voice_mode, preferences.voice_mode), "
            "voice_frequency = COALESCE(excluded.voice_frequency, preferences.voice_frequency), "
            "images_enabled = COALESCE(excluded.images_enabled, preferences.images_enabled), "
            "updated_at = excluded.updated_at",
            (scope_id, str(user_id), voice_mode, voice_frequency, images_enabled, now),
        )
