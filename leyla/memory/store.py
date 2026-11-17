"""Long-term memory: storage, deduplication and relevance retrieval.

Deliberately dependency-free: retrieval is a small lexical ranker (token
overlap + importance + recency + usage) instead of an embedding index, which
keeps RAM and API costs at zero while still surfacing the right facts.
"""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass

from ..db import Database
from ..logging_setup import get_logger

log = get_logger("leyla.memory")

VALID_KINDS = ("fact", "preference", "person", "date", "plan", "boundary", "topic")

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "had", "has",
    "have", "he", "her", "his", "i", "if", "in", "is", "it", "its", "me", "my", "not",
    "of", "on", "or", "she", "so", "that", "the", "their", "them", "then", "there",
    "they", "this", "to", "was", "we", "were", "what", "when", "which", "who", "will",
    "with", "you", "your", "im", "ive", "dont", "didnt", "just", "really", "about",
}

_WORD_RE = re.compile(r"[a-z0-9']+")


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().lower()


def fingerprint(content: str, subject: str | None = None) -> str:
    """Stable hash used to reject duplicate memories."""
    collapsed = re.sub(r"\s+", " ", (content or "").strip())
    basis = _normalise((subject or "") + "|" + collapsed)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def tokenize(text: str) -> set[str]:
    return {
        token for token in _WORD_RE.findall(_normalise(text))
        if token not in _STOPWORDS and len(token) > 1
    }


def _similarity(left: set[str], right: set[str]) -> float:
    """Jaccard overlap of two memory token sets."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def bigrams(text: str) -> set[str]:
    """Adjacent content-word pairs, used to reward exact phrasing matches."""
    words = [
        token for token in _WORD_RE.findall(_normalise(text))
        if token not in _STOPWORDS and len(token) > 1
    ]
    return {f"{first} {second}" for first, second in zip(words, words[1:])}


def _root(token: str) -> str:
    """Crude suffix stripping so ``dogs``/``called``/``running`` match their base."""
    for suffix in ("ing", "ied", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def token_hits(query_tokens: set[str], memory_tokens: set[str]) -> int:
    """Count query terms present in a memory, tolerating simple word forms.

    ``dogs``/``dog`` and ``called``/``call`` are the same thought to a reader, so
    roots and short prefixes count as a match. Four characters is the floor that
    keeps ``car`` from matching ``cartoon``.
    """
    if not query_tokens or not memory_tokens:
        return 0
    roots = {_root(token) for token in memory_tokens}
    hits = 0
    for query_token in query_tokens:
        root = _root(query_token)
        if root in roots:
            hits += 1
            continue
        if len(root) >= 4 and any(
            root.startswith(other) or other.startswith(root) for other in roots
        ):
            hits += 1
    return hits


@dataclass(slots=True)
class Memory:
    id: int
    kind: str
    subject: str | None
    content: str
    importance: float
    created_at: float
    updated_at: float
    use_count: int = 0
    last_used_at: float | None = None

    def describe(self) -> str:
        return f"#{self.id} · {self.kind} · {int(self.importance * 100)}% · {self.content}"


class MemoryStore:
    """CRUD + search for long-term memories, scoped per owner."""

    def __init__(self, db: Database, *, max_per_scope: int = 400) -> None:
        self.db = db
        self.max_per_scope = max_per_scope

    # ----------------------------------------------------------------- write
    async def add(
        self,
        scope_id: str,
        content: str,
        *,
        kind: str = "fact",
        importance: float = 0.5,
        subject: str | None = None,
        source: str | None = None,
    ) -> tuple[Memory | None, bool]:
        """Insert a memory; returns ``(memory, created)``.

        Duplicates (same normalised content) return the existing row with
        ``created=False`` and a slightly boosted importance.
        """
        content = (content or "").strip()
        if len(content) < 3:
            return None, False
        content = content[:500]
        kind = kind if kind in VALID_KINDS else "fact"
        importance = max(0.0, min(1.0, float(importance)))
        now = time.time()
        fp = fingerprint(content, subject)

        existing = await self.db.fetchone(
            "SELECT * FROM memories WHERE scope_id = ? AND fingerprint = ?", (scope_id, fp)
        )
        if existing is not None:
            await self.db.execute(
                "UPDATE memories SET importance = MIN(1.0, importance + 0.05), updated_at = ? WHERE id = ?",
                (now, existing["id"]),
            )
            return self._row_to_memory(existing), False

        memory_id = await self.db.execute(
            "INSERT INTO memories(scope_id, kind, subject, content, importance, fingerprint, source, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (scope_id, kind, subject, content, importance, fp, source, now, now),
        )
        await self._enforce_cap(scope_id)
        memory = await self.get(scope_id, memory_id)
        log.info("memory stored #%s [%s] %s", memory_id, kind, content[:80])
        return memory, True

    async def update(
        self,
        scope_id: str,
        memory_id: int,
        *,
        content: str | None = None,
        importance: float | None = None,
        kind: str | None = None,
    ) -> Memory | None:
        row = await self.db.fetchone(
            "SELECT * FROM memories WHERE scope_id = ? AND id = ?", (scope_id, memory_id)
        )
        if row is None:
            return None
        new_content = (content or row["content"])[:500]
        new_kind = kind if kind in VALID_KINDS else row["kind"]
        new_importance = (
            float(row["importance"]) if importance is None else max(0.0, min(1.0, importance))
        )
        await self.db.execute(
            "UPDATE memories SET content = ?, kind = ?, importance = ?, fingerprint = ?, updated_at = ? "
            "WHERE scope_id = ? AND id = ?",
            (
                new_content,
                new_kind,
                new_importance,
                fingerprint(new_content, row["subject"]),
                time.time(),
                scope_id,
                memory_id,
            ),
        )
        return await self.get(scope_id, memory_id)

    async def supersede(
        self,
        scope_id: str,
        old_id: int,
        new_content: str,
        *,
        kind: str = "fact",
        importance: float = 0.6,
        subject: str | None = None,
    ) -> tuple[Memory | None, bool]:
        """Replace an outdated memory with a corrected one."""
        await self.delete(scope_id, old_id)
        return await self.add(
            scope_id,
            new_content,
            kind=kind,
            importance=importance,
            subject=subject,
            source=f"superseded:{old_id}",
        )

    # ------------------------------------------------------------------ read
    async def get(self, scope_id: str, memory_id: int) -> Memory | None:
        row = await self.db.fetchone(
            "SELECT * FROM memories WHERE scope_id = ? AND id = ?", (scope_id, memory_id)
        )
        return self._row_to_memory(row) if row else None

    async def list(
        self,
        scope_id: str,
        *,
        limit: int = 25,
        kind: str | None = None,
        offset: int = 0,
    ) -> list[Memory]:
        if kind:
            rows = await self.db.fetchall(
                "SELECT * FROM memories WHERE scope_id = ? AND kind = ? "
                "ORDER BY importance DESC, updated_at DESC LIMIT ? OFFSET ?",
                (scope_id, kind, limit, offset),
            )
        else:
            rows = await self.db.fetchall(
                "SELECT * FROM memories WHERE scope_id = ? "
                "ORDER BY importance DESC, updated_at DESC LIMIT ? OFFSET ?",
                (scope_id, limit, offset),
            )
        return [self._row_to_memory(row) for row in rows]

    async def count(self, scope_id: str) -> int:
        return int(await self.db.scalar("SELECT COUNT(*) FROM memories WHERE scope_id = ?", (scope_id,)) or 0)

    async def search(
        self,
        scope_id: str,
        query: str,
        *,
        limit: int = 6,
        context: str = "",
        include_recent: bool = True,
        now: float | None = None,
    ) -> list[tuple[Memory, float]]:
        """Return the memories most relevant to ``query`` with their scores.

        ``context`` is the live thread (recent user lines). It carries less
        weight than the message itself, but it is what lets a follow-up like
        "what about Thursday?" still surface a fact from two messages ago.
        """
        now = now if now is not None else time.time()
        rows = await self.db.fetchall(
            "SELECT * FROM memories WHERE scope_id = ? ORDER BY importance DESC",
            (scope_id,),
        )
        if not rows:
            return []

        query_tokens = tokenize(query)
        context_tokens = tokenize(context) - query_tokens
        phrases = bigrams(query) | bigrams(context)

        scored: list[tuple[Memory, float]] = []
        for row in rows:
            memory = self._row_to_memory(row)
            memory_tokens = tokenize(f"{memory.subject or ''} {memory.content}")
            overlap = token_hits(query_tokens, memory_tokens)
            coverage = overlap / max(1, len(query_tokens)) if query_tokens else 0.0
            context_overlap = token_hits(context_tokens, memory_tokens)
            context_coverage = (
                context_overlap / max(1, len(context_tokens)) if context_tokens else 0.0
            )
            text = _normalise(f"{memory.subject or ''} {memory.content}")
            phrase_hits = sum(1 for phrase in phrases if phrase in text)
            subject_hit = (
                0.15
                if memory.subject and _normalise(memory.subject) in _normalise(query)
                else 0.0
            )
            age_days = max(0.0, (now - memory.updated_at) / 86400)
            recency = 1.0 / (1.0 + age_days / 21.0)
            usage = 1.0 / (1.0 + 0.10 * memory.use_count)
            score = (
                0.50 * coverage
                + 0.12 * context_coverage
                + 0.16 * memory.importance
                + 0.09 * recency
                + 0.03 * usage
                + subject_hit
                + min(0.30, 0.15 * phrase_hits)
            )

            matched = overlap or context_overlap or phrase_hits
            if not matched:
                # Nothing textual: only boundaries, load-bearing facts and very
                # recent preferences earn a slot, and never above a real match.
                critical = memory.kind == "boundary" or memory.importance >= 0.8
                recent_preference = memory.kind == "preference" and age_days <= 7
                if not critical and not (include_recent and recent_preference):
                    continue
                score = min(score, 0.24)
            scored.append((memory, round(score, 4)))

        scored.sort(key=lambda item: item[1], reverse=True)

        # Diversity: five near-identical gym notes are worse than one gym note
        # plus the rest of their life. Similar memories wait in ``overflow`` and
        # only fill space nobody else wanted.
        selected: list[tuple[Memory, float]] = []
        overflow: list[tuple[Memory, float]] = []
        selected_tokens: list[set[str]] = []
        per_subject: dict[str, int] = {}
        for memory, score in scored:
            tokens = tokenize(f"{memory.subject or ''} {memory.content}")
            key = _normalise(memory.subject or "")
            too_similar = any(
                _similarity(tokens, taken) >= 0.6 for taken in selected_tokens
            )
            if too_similar or (key and per_subject.get(key, 0) >= 2):
                overflow.append((memory, score))
                continue
            if key:
                per_subject[key] = per_subject.get(key, 0) + 1
            selected.append((memory, score))
            selected_tokens.append(tokens)
            if len(selected) >= limit:
                break
        if len(selected) < limit:
            selected.extend(overflow[: limit - len(selected)])
        selected = selected[:limit]

        if selected:
            await self.touch([memory.id for memory, _ in selected])
        return selected

    async def touch(self, ids: list[int]) -> None:
        if not ids:
            return
        now = time.time()
        await self.db.executemany(
            "UPDATE memories SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
            [(now, memory_id) for memory_id in ids],
        )

    # ---------------------------------------------------------------- delete
    async def delete(self, scope_id: str, memory_id: int) -> bool:
        exists = await self.db.fetchone(
            "SELECT id FROM memories WHERE scope_id = ? AND id = ?", (scope_id, memory_id)
        )
        if exists is None:
            return False
        await self.db.execute(
            "DELETE FROM memories WHERE scope_id = ? AND id = ?", (scope_id, memory_id)
        )
        log.info("memory #%s deleted", memory_id)
        return True

    async def clear(self, scope_id: str) -> int:
        count = await self.count(scope_id)
        await self.db.execute("DELETE FROM memories WHERE scope_id = ?", (scope_id,))
        log.info("cleared %s memories", count)
        return count

    async def _enforce_cap(self, scope_id: str) -> None:
        """Keep the store bounded: drop the weakest memories beyond the cap."""
        total = await self.count(scope_id)
        if total <= self.max_per_scope:
            return
        excess = total - self.max_per_scope
        await self.db.execute(
            "DELETE FROM memories WHERE id IN (SELECT id FROM memories WHERE scope_id = ? "
            "ORDER BY importance ASC, updated_at ASC LIMIT ?)",
            (scope_id, excess),
        )
        log.info("pruned %s low-value memories", excess)

    # ---------------------------------------------------------------- helper
    @staticmethod
    def _row_to_memory(row) -> Memory:
        return Memory(
            id=int(row["id"]),
            kind=row["kind"],
            subject=row["subject"],
            content=row["content"],
            importance=float(row["importance"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            use_count=int(row["use_count"] or 0),
            last_used_at=float(row["last_used_at"]) if row["last_used_at"] else None,
        )


def relevance_summary(memories: list[tuple[Memory, float]]) -> str:
    """Compact one-line summary used in logs/diagnostics."""
    if not memories:
        return "no relevant memories"
    return ", ".join(f"#{m.id}:{score:.2f}" for m, score in memories)


__all__ = [
    "Memory",
    "MemoryStore",
    "bigrams",
    "fingerprint",
    "token_hits",
    "tokenize",
    "relevance_summary",
    "VALID_KINDS",
]
