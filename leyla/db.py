"""SQLite storage layer.

Why SQLite: KataBump's free plan provides no database, but it does give
persistent disk (716 MB). SQLite needs no server, survives restarts, and keeps
the footprint tiny — ``aiosqlite`` is avoided on purpose to save RAM.

All queries run through :class:`Database`, which serialises access on a single
connection (guarded by a lock) and offloads blocking work to a worker thread.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from .logging_setup import get_logger

log = get_logger("leyla.db")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Long-term memory ------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id      TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'fact',
    subject       TEXT,
    content       TEXT NOT NULL,
    importance    REAL NOT NULL DEFAULT 0.5,
    fingerprint   TEXT NOT NULL,
    source        TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    last_used_at  REAL,
    use_count     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (scope_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope_id, importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_kind  ON memories(scope_id, kind);

-- Short-term conversation log ------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id   TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    channel_id TEXT,
    message_id TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(scope_id, id DESC);

-- Mood state ------------------------------------------------------
CREATE TABLE IF NOT EXISTS mood_state (
    scope_id   TEXT PRIMARY KEY,
    mood       TEXT NOT NULL,
    intensity  REAL NOT NULL,
    reason     TEXT,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    changes    INTEGER NOT NULL DEFAULT 0
);

-- Per-user / per-scope preferences -------------------------------
CREATE TABLE IF NOT EXISTS preferences (
    scope_id       TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    voice_mode     TEXT,
    voice_frequency REAL,
    images_enabled INTEGER,
    updated_at     REAL NOT NULL,
    PRIMARY KEY (scope_id, user_id)
);

-- Global key/value settings (also used by /setup) ----------------
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Usage accounting for budgets -----------------------------------
CREATE TABLE IF NOT EXISTS usage (
    period TEXT NOT NULL,
    kind   TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (period, kind)
);

-- Cooldowns (voice notes, nonverbal tags, ...) --------------------
CREATE TABLE IF NOT EXISTS cooldowns (
    key    TEXT PRIMARY KEY,
    ends_at REAL NOT NULL
);
"""


class Database:
    """Small async facade over a single SQLite connection."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------ lifecycle
    def connect(self) -> None:
        if self._conn is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        self._conn = conn
        self._run_migrations()
        log.info("database ready at %s (schema v%s)", self.path, SCHEMA_VERSION)

    def _run_migrations(self) -> None:
        assert self._conn is not None
        current = self._scalar_sync("SELECT value FROM meta WHERE key='schema_version'")
        version = int(current) if current is not None else 0
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema v{version} is newer than this build (v{SCHEMA_VERSION})"
            )
        # Future migrations go here, applied in order.
        if version < SCHEMA_VERSION:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(SCHEMA_VERSION),),
                )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:  # pragma: no cover - best effort
                    pass
                self._conn.close()
                self._conn = None
                log.info("database closed")

    # ------------------------------------------------------------- plumbing
    def _scalar_sync(self, sql: str, params: Sequence[Any] = ()) -> Any:
        assert self._conn is not None, "Database.connect() must be called first"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def _execute_sync(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        assert self._conn is not None, "Database.connect() must be called first"
        with self._lock:
            return self._conn.execute(sql, params)

    def _fetchall_sync(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        assert self._conn is not None, "Database.connect() must be called first"
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a statement; returns ``lastrowid``."""
        if self._conn is None:
            self.connect()

        def run() -> int:
            cursor = self._execute_sync(sql, params)
            return int(cursor.lastrowid or 0)

        return await asyncio.to_thread(run)

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        if self._conn is None:
            self.connect()
        rows = list(rows)

        def run() -> None:
            assert self._conn is not None
            with self._lock:
                self._conn.executemany(sql, rows)

        await asyncio.to_thread(run)

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        if self._conn is None:
            self.connect()
        return await asyncio.to_thread(
            lambda: (self._fetchall_sync(sql, params) or [None])[0]
        )

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        if self._conn is None:
            self.connect()
        return await asyncio.to_thread(self._fetchall_sync, sql, params)

    async def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = await self.fetchone(sql, params)
        return None if row is None else row[0]

    # --------------------------------------------------------- convenience
    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = await self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return default if row is None else str(row["value"])

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, str(value), _now()),
        )

    async def delete_setting(self, key: str) -> None:
        await self.execute("DELETE FROM settings WHERE key = ?", (key,))

    async def increment_usage(self, period: str, kind: str, amount: int) -> int:
        if amount == 0:
            row = await self.fetchone("SELECT amount FROM usage WHERE period=? AND kind=?", (period, kind))
            return int(row["amount"]) if row else 0
        await self.execute(
            "INSERT INTO usage(period, kind, amount) VALUES(?,?,?) "
            "ON CONFLICT(period, kind) DO UPDATE SET amount = amount + excluded.amount",
            (period, kind, int(amount)),
        )
        return int(await self.scalar("SELECT amount FROM usage WHERE period=? AND kind=?", (period, kind)) or 0)

    async def get_usage(self, period: str, kind: str) -> int:
        return int(await self.scalar("SELECT amount FROM usage WHERE period=? AND kind=?", (period, kind)) or 0)

    async def get_cooldown(self, key: str, *, now: float | None = None) -> float:
        """Seconds remaining on a cooldown (0 when free)."""
        row = await self.fetchone("SELECT ends_at FROM cooldowns WHERE key=?", (key,))
        if row is None:
            return 0.0
        return max(0.0, float(row["ends_at"]) - (_now() if now is None else now))

    async def set_cooldown(self, key: str, seconds: float) -> None:
        ends_at = _now() + max(0.0, float(seconds))
        await self.execute(
            "INSERT INTO cooldowns(key, ends_at) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET ends_at=excluded.ends_at",
            (key, ends_at),
        )


def _now() -> float:
    import time

    return time.time()
