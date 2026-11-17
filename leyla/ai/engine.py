"""Conversation engine.

One provider call per turn produces everything the rest of the system needs:
the reply text, a mood suggestion, a voice hint and durable memory candidates.
That keeps the cost at a single request while still giving Leyla continuity.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from ..config import Settings
from ..db import Database
from ..errors import BudgetExceededError, ProviderError
from ..logging_setup import get_logger
from ..memory.context import ConversationStore
from ..memory.extractor import MemoryCandidate, MemoryExtractor, parse_candidates
from ..memory.store import Memory, MemoryStore, relevance_summary
from ..mood.engine import MoodEngine, MoodSuggestion
from ..mood.models import MoodState, coerce_mood, spec_for
from ..personality.profile import build_system_prompt
from .base import ChatMessage, ChatRequest, ChatResponse
from .registry import ProviderChain

log = get_logger("leyla.engine")

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_LEADING_NAME = re.compile(r"^\s*(leyla|assistant|bot)\s*[:\-]\s*", re.IGNORECASE)
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_MARKDOWN = re.compile(r"(\*\*|__|~~|`{1,2})")


@dataclass(slots=True)
class VoiceHint:
    send: bool = False
    energy: str = "medium"
    delivery: str = ""

    @classmethod
    def from_payload(cls, payload: object) -> "VoiceHint":
        if not isinstance(payload, dict):
            return cls()
        energy = str(payload.get("energy") or "medium").lower()
        if energy not in {"low", "medium", "high"}:
            energy = "medium"
        return cls(
            send=bool(payload.get("send")),
            energy=energy,
            delivery=str(payload.get("delivery") or "")[:120],
        )


@dataclass(slots=True)
class TurnResult:
    reply: str
    mood: MoodState
    voice_hint: VoiceHint
    provider: str
    model: str
    latency_ms: int = 0
    saved_memories: list[Memory] = field(default_factory=list)
    memory_candidates: list[MemoryCandidate] = field(default_factory=list)
    fallback_extraction: bool = False
    parsed_envelope: bool = True


@dataclass(slots=True)
class ParsedTurn:
    reply: str
    mood: MoodSuggestion | None
    voice: VoiceHint
    memories: list[MemoryCandidate]
    parsed: bool


def parse_turn_payload(text: str) -> ParsedTurn:
    """Parse the model's JSON envelope, degrading gracefully to plain text."""
    raw = (text or "").strip()
    cleaned = _CODE_FENCE.sub("", raw).strip()
    data: object = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                data = None

    if isinstance(data, dict) and isinstance(data.get("reply"), str) and data["reply"].strip():
        mood_payload = data.get("mood")
        mood = None
        if isinstance(mood_payload, dict):
            mood_value = coerce_mood(mood_payload.get("name"))
            if mood_value is not None:
                try:
                    intensity = max(0.0, min(1.0, float(mood_payload.get("intensity", 0.5))))
                except (TypeError, ValueError):
                    intensity = 0.5
                mood = MoodSuggestion(
                    mood=mood_value,
                    intensity=intensity,
                    reason=str(mood_payload.get("why") or "")[:80],
                )
        return ParsedTurn(
            reply=clean_reply(data["reply"]),
            mood=mood,
            voice=VoiceHint.from_payload(data.get("voice")),
            memories=parse_candidates(json.dumps({"memories": data.get("remember") or []})),
            parsed=True,
        )

    # Plain text fallback (some fallback models ignore JSON mode).
    plain = clean_reply(cleaned)
    return ParsedTurn(reply=plain, mood=None, voice=VoiceHint(), memories=[], parsed=False)


def clean_reply(text: str) -> str:
    """Strip model artefacts that would look wrong in Discord."""
    reply = (text or "").strip()
    reply = _CODE_FENCE.sub("", reply).strip()
    reply = _LEADING_NAME.sub("", reply).strip()
    if reply.startswith('"') and reply.endswith('"') and len(reply) > 2:
        reply = reply[1:-1].strip()
    reply = _MULTI_NEWLINE.sub("\n\n", reply)
    return reply.strip()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class ConversationEngine:
    """Owns prompt assembly, provider calls and post-processing."""

    def __init__(
        self,
        settings: Settings,
        chain: ProviderChain,
        db: Database,
        memories: MemoryStore,
        conversations: ConversationStore,
        mood_engine: MoodEngine,
        extractor: MemoryExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.chain = chain
        self.db = db
        self.memories = memories
        self.conversations = conversations
        self.mood_engine = mood_engine
        self.extractor = extractor

    async def respond(
        self,
        scope_id: str,
        user_text: str,
        *,
        display_name: str | None = None,
        channel_id: int | None = None,
        message_id: int | None = None,
        voice_enabled: bool = True,
        local_time: str | None = None,
    ) -> TurnResult:
        user_text = (user_text or "").strip()
        if not user_text:
            raise ValueError("empty message")

        await self._check_budget()

        mood_state = await self.mood_engine.get(scope_id)
        history = await self.conversations.recent(
            scope_id, limit=max(4, self.settings.chat_history_turns * 2)
        )
        relevant = await self.memories.search(scope_id, user_text, limit=6)
        system_prompt = build_system_prompt(
            mood=mood_state,
            memories=relevant,
            display_name=display_name,
            local_time=local_time or _local_time_hint(),
            voice_enabled=voice_enabled,
            recent_topics=await self.conversations.recent_topics(scope_id, limit=3),
        )

        messages = [ChatMessage("system", system_prompt)]
        messages.extend(ChatMessage(item["role"], item["content"]) for item in history)
        messages.append(ChatMessage("user", user_text))

        request = ChatRequest(
            messages=messages,
            model=self.settings.ai_model,
            temperature=self.settings.ai_temperature,
            max_tokens=self.settings.ai_max_output_tokens,
            json_mode=True,
            timeout=float(self.settings.ai_timeout_seconds),
            purpose="chat",
        )

        started = time.monotonic()
        response = await self.chain.complete(request)
        latency_ms = int((time.monotonic() - started) * 1000)
        await self.db.increment_usage(_today(), "llm", 1)

        parsed = parse_turn_payload(response.text)
        if not parsed.reply:
            parsed.reply = "…I had something for that and it vanished. say it again?"

        mood_after = await self.mood_engine.apply(
            scope_id, parsed.mood, user_text=user_text
        )

        await self.conversations.add(
            scope_id, "user", user_text, channel_id=channel_id, message_id=message_id
        )
        await self.conversations.add(scope_id, "assistant", parsed.reply, channel_id=channel_id)

        saved: list[Memory] = []
        candidates = parsed.memories
        used_fallback = False

        if not candidates and not parsed.parsed and self.settings.memory_extraction_enabled:
            if self.extractor is not None and MemoryExtractor.should_attempt(user_text):
                candidates = await self.extractor.extract(
                    user_text=user_text,
                    assistant_text=parsed.reply,
                    existing=relevant,
                )
                used_fallback = True
                if candidates:
                    await self.db.increment_usage(_today(), "llm", 1)

        saved = await self._persist_candidates(scope_id, candidates)

        log.info(
            "turn provider=%s model=%s mood=%s/%.2f memories=%s latency=%dms%s",
            response.provider,
            response.model,
            mood_after.mood.value,
            mood_after.intensity,
            relevance_summary([(m, 1.0) for m in saved]) if saved else "none",
            latency_ms,
            " (fallback extraction)" if used_fallback else "",
        )

        return TurnResult(
            reply=parsed.reply,
            mood=mood_after,
            voice_hint=parsed.voice,
            provider=response.provider,
            model=response.model,
            latency_ms=latency_ms,
            saved_memories=saved,
            memory_candidates=candidates,
            fallback_extraction=used_fallback,
            parsed_envelope=parsed.parsed,
        )

    async def _persist_candidates(
        self, scope_id: str, candidates: list[MemoryCandidate]
    ) -> list[Memory]:
        saved: list[Memory] = []
        for candidate in candidates[:3]:
            if candidate.supersedes_id is not None:
                existing = await self.memories.get(scope_id, candidate.supersedes_id)
                if existing is not None:
                    memory, created = await self.memories.supersede(
                        scope_id,
                        candidate.supersedes_id,
                        candidate.content,
                        kind=candidate.kind,
                        importance=candidate.importance,
                        subject=candidate.subject,
                    )
                else:
                    memory, created = await self.memories.add(
                        scope_id,
                        candidate.content,
                        kind=candidate.kind,
                        importance=candidate.importance,
                        subject=candidate.subject,
                        source="model",
                    )
            else:
                memory, created = await self.memories.add(
                    scope_id,
                    candidate.content,
                    kind=candidate.kind,
                    importance=candidate.importance,
                    subject=candidate.subject,
                    source="model",
                )
            if created and memory is not None:
                saved.append(memory)
        return saved

    async def _check_budget(self) -> None:
        used = await self.db.get_usage(_today(), "llm")
        if used >= self.settings.ai_daily_request_limit:
            raise BudgetExceededError(
                f"daily AI request budget reached ({used}/{self.settings.ai_daily_request_limit})"
            )

    async def reset_conversation(self, scope_id: str) -> int:
        """Forget the short-term window but keep long-term memories and mood."""
        return await self.conversations.clear(scope_id)

    def describe(self) -> str:
        return f"{' → '.join(self.chain.names)} ({self.settings.ai_model})"


def _local_time_hint() -> str:
    now = datetime.now()
    return now.strftime("%A %H:%M")


__all__ = [
    "ConversationEngine",
    "ParsedTurn",
    "TurnResult",
    "VoiceHint",
    "clean_reply",
    "parse_turn_payload",
    "spec_for",
]
