"""Runtime-changeable settings (channel whitelist, feature toggles)."""

from __future__ import annotations

import json
import time

from .db import Database
from .logging_setup import get_logger

log = get_logger("leyla.runtime")


class RuntimeConfig:
    """Small key/value layer on top of SQLite for /setup-driven changes."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, key: str, default: str | None = None) -> str | None:
        return await self.db.get_setting(key, default)

    async def set(self, key: str, value: str) -> None:
        await self.db.set_setting(key, value)

    # ------------------------------------------------- channel whitelist
    def _key(self, guild_id: int | str) -> str:
        return f"channels:{guild_id}"

    async def allowed_channels(self, guild_id: int | str) -> set[int]:
        raw = await self.db.get_setting(self._key(guild_id))
        if not raw:
            return set()
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return set()
        return {int(v) for v in values if str(v).isdigit()}

    async def add_channel(self, guild_id: int | str, channel_id: int) -> set[int]:
        channels = await self.allowed_channels(guild_id)
        channels.add(int(channel_id))
        await self.db.set_setting(self._key(guild_id), json.dumps(sorted(channels)))
        return channels

    async def remove_channel(self, guild_id: int | str, channel_id: int) -> set[int]:
        channels = await self.allowed_channels(guild_id)
        channels.discard(int(channel_id))
        await self.db.set_setting(self._key(guild_id), json.dumps(sorted(channels)))
        return channels

    async def clear_channels(self, guild_id: int | str) -> None:
        await self.db.delete_setting(self._key(guild_id))

    async def reset_all(self) -> None:
        """Forget runtime overrides (preferences and memories are separate)."""
        await self.db.execute("DELETE FROM settings WHERE key LIKE 'channels:%'")
        log.info("runtime channel settings cleared at %s", time.time())
