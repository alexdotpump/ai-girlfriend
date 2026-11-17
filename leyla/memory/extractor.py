"""Memory extraction fallback.

The primary model call already returns ``remember`` items (cheapest path: one
request per turn). This extractor exists for the cases where that envelope is
unavailable — plain-text provider fallback, or a malformed reply — so memory
still works instead of silently degrading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..ai.base import ChatMessage, ChatRequest
from ..ai.registry import ProviderChain
from ..errors import ProviderError
from ..logging_setup import get_logger
from ..personality.profile import build_extraction_prompt
from .store import VALID_KINDS, Memory

log = get_logger("leyla.memory.extract")


@dataclass(slots=True)
class MemoryCandidate:
    kind: str
    content: str
    importance: float = 0.5
    subject: str | None = None
    supersedes_id: int | None = None


class MemoryExtractor:
    def __init__(self, chain: ProviderChain, *, model: str = "", timeout: float = 30.0) -> None:
        self.chain = chain
        self.model = model
        self.timeout = timeout

    @staticmethod
    def should_attempt(user_text: str) -> bool:
        """Skip obvious noise so we do not spend a request on 'lol'."""
        text = (user_text or "").strip()
        if len(text) < 12:
            return False
        if text.startswith("/"):
            return False
        return any(ch.isalpha() for ch in text)

    async def extract(
        self,
        *,
        user_text: str,
        assistant_text: str,
        existing: list[tuple[Memory, float]] | None = None,
    ) -> list[MemoryCandidate]:
        if not self.should_attempt(user_text):
            return []

        exchange = f"THEM: {user_text}\nLEYLA: {assistant_text}"
        system = build_extraction_prompt(exchange=exchange, existing=existing)
        request = ChatRequest(
            messages=[
                ChatMessage("system", system),
                ChatMessage("user", "Return the JSON now."),
            ],
            model=self.model,
            temperature=0.2,
            max_tokens=260,
            json_mode=True,
            timeout=self.timeout,
            purpose="memory",
        )
        try:
            response = await self.chain.complete(request)
        except ProviderError as exc:
            log.warning("memory extraction failed: %s", exc)
            return []

        return parse_candidates(response.text, valid_ids={m.id for m, _ in (existing or [])})


def parse_candidates(payload: str, *, valid_ids: set[int] | None = None) -> list[MemoryCandidate]:
    """Parse the extractor's JSON, tolerating code fences and stray prose.

    ``valid_ids=None`` means "do not constrain supersede targets" (the caller
    validates them against the store); passing a set restricts them.
    """
    data = _load_json(payload)
    if not isinstance(data, dict):
        return []
    raw_items = data.get("memories") or data.get("remember") or []
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return []

    candidates: list[MemoryCandidate] = []
    for item in raw_items[:3]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if len(content) < 4:
            continue
        kind = str(item.get("kind") or "fact").strip().lower()
        if kind not in VALID_KINDS:
            kind = "fact"
        try:
            importance = max(0.0, min(1.0, float(item.get("importance", 0.5))))
        except (TypeError, ValueError):
            importance = 0.5
        supersedes = item.get("supersedes_id")
        try:
            supersedes_id = int(supersedes) if supersedes not in (None, "", "null") else None
        except (TypeError, ValueError):
            supersedes_id = None
        if supersedes_id is not None and valid_ids is not None and supersedes_id not in valid_ids:
            supersedes_id = None
        subject = item.get("subject")
        candidates.append(
            MemoryCandidate(
                kind=kind,
                content=content[:400],
                importance=importance,
                subject=str(subject)[:80] if subject else None,
                supersedes_id=supersedes_id,
            )
        )
    return candidates


def _load_json(payload: str) -> object:
    if not payload:
        return {}
    text = payload.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
    return {}
