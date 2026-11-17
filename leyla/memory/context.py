"""Short-term conversation context.

Only a bounded window is kept in SQLite: enough for coherent multi-message
conversation, far too little to become a surveillance log. Old rows are pruned
on every append, and ``/memory clear`` wipes this table too.
"""

from __future__ import annotations

import time

from ..db import Database
from ..logging_setup import get_logger

log = get_logger("leyla.context")

MAX_ROWS_PER_SCOPE = 80
MAX_AGE_DAYS = 14


class ConversationStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        scope_id: str,
        role: str,
        content: str,
        *,
        channel_id: int | str | None = None,
        message_id: int | str | None = None,
    ) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("role must be 'user' or 'assistant'")
        await self.db.execute(
            "INSERT INTO messages(scope_id, role, content, channel_id, message_id, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                scope_id,
                role,
                content[:4000],
                str(channel_id) if channel_id else None,
                str(message_id) if message_id else None,
                time.time(),
            ),
        )
        await self._prune(scope_id)

    async def recent(self, scope_id: str, *, limit: int = 20) -> list[dict[str, str]]:
        rows = await self.db.fetchall(
            "SELECT role, content FROM messages WHERE scope_id = ? ORDER BY id DESC LIMIT ?",
            (scope_id, limit),
        )
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    async def recent_topics(self, scope_id: str, *, limit: int = 5) -> list[str]:
        """Very short recap of recent user lines, used to keep threads alive."""
        rows = await self.db.fetchall(
            "SELECT content FROM messages WHERE scope_id = ? AND role = 'user' "
            "ORDER BY id DESC LIMIT ?",
            (scope_id, limit),
        )
        topics: list[str] = []
        for row in rows:
            text = " ".join(str(row["content"]).split())
            if len(text) > 90:
                text = text[:87] + "…"
            if text:
                topics.append(text)
        return topics

    async def seen_message(self, scope_id: str, message_id: int | str) -> bool:
        """Duplicate-delivery guard for gateway reconnects."""
        row = await self.db.fetchone(
            "SELECT id FROM messages WHERE scope_id = ? AND message_id = ?",
            (scope_id, str(message_id)),
        )
        return row is not None

    async def clear(self, scope_id: str) -> int:
        count = int(
            await self.db.scalar("SELECT COUNT(*) FROM messages WHERE scope_id = ?", (scope_id,)) or 0
        )
        await self.db.execute("DELETE FROM messages WHERE scope_id = ?", (scope_id,))
        return count

    async def stats(self, scope_id: str) -> dict[str, int]:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS total, MIN(created_at) AS oldest, MAX(created_at) AS newest "
            "FROM messages WHERE scope_id = ?",
            (scope_id,),
        )
        return {
            "count": int(row["total"] or 0) if row else 0,
            "oldest": int(row["oldest"] or 0) if row else 0,
            "newest": int(row["newest"] or 0) if row else 0,
        }

    async def _prune(self, scope_id: str) -> None:
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        await self.db.execute(
            "DELETE FROM messages WHERE scope_id = ? AND created_at < ?", (scope_id, cutoff)
        )
        await self.db.execute(
            "DELETE FROM messages WHERE scope_id = ? AND id NOT IN "
            "(SELECT id FROM messages WHERE scope_id = ? ORDER BY id DESC LIMIT ?)",
            (scope_id, scope_id, MAX_ROWS_PER_SCOPE),
        )
