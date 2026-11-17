"""One conversational turn, from text to (optional) voice note."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import discord

from .ai.engine import TurnResult
from .errors import BudgetExceededError, ProviderError, RateLimitedError, VoiceError
from .logging_setup import get_logger
from .services import Services
from .utils.text import chunk_text, looks_like_voice_request

log = get_logger("leyla.chat_flow")


@dataclass(slots=True)
class ChatOutcome:
    turn: TurnResult | None
    voice_sent: bool
    voice_reason: str = ""
    text_sent: bool = True
    chunks: int = 0
    elapsed_ms: int = 0


async def run_turn(
    *,
    services: Services,
    bot: discord.Client,
    channel: discord.abc.Messageable,
    sender: Callable[[str], Awaitable[None]],
    scope_id: str,
    user_text: str,
    display_name: str,
    channel_id: int | None = None,
    message_id: int | None = None,
    explicit_voice: bool = False,
) -> ChatOutcome:
    """Generate Leyla's reply, deliver it, and attach a voice note when apt."""
    started = time.monotonic()

    services.limiter.check(scope_id)

    prefs = await services.preferences.get(scope_id, scope_id)
    voice_mode = prefs.voice_mode
    voice_enabled = services.voice.enabled and voice_mode != "off"

    wants_voice = explicit_voice or looks_like_voice_request(user_text)

    turn = await services.engine.respond(
        scope_id,
        user_text,
        display_name=display_name,
        channel_id=channel_id,
        message_id=message_id,
        voice_enabled=voice_enabled,
    )

    voice_sent = False
    voice_reason = ""
    text_sent = False
    chunks = 0

    # An explicit request replaces the text reply with the spoken one.
    if wants_voice and voice_enabled:
        result = await services.voice.maybe_send(
            channel=channel,
            bot=bot,
            scope_id=scope_id,
            reply=turn.reply,
            mood=turn.mood,
            request="explicit",
            mode=voice_mode,
            frequency=prefs.voice_frequency,
            energy=turn.voice_hint.energy,
        )
        voice_sent = result.sent
        voice_reason = result.reason
        if not result.sent:
            log.warning("explicit voice note failed: %s", result.reason)

    if not voice_sent:
        parts = chunk_text(turn.reply, limit=services.settings.chat_max_message_chars)
        for part in parts:
            await sender(part)
        chunks = len(parts)
        text_sent = True

    # Contextual, occasional voice notes are additive to the text reply.
    if not wants_voice and voice_enabled and text_sent:
        result = await services.voice.maybe_send(
            channel=channel,
            bot=bot,
            scope_id=scope_id,
            reply=turn.reply,
            mood=turn.mood,
            request="auto",
            mode=voice_mode,
            frequency=prefs.voice_frequency,
            energy=turn.voice_hint.energy,
        )
        voice_sent = result.sent
        voice_reason = result.reason

    elapsed = int((time.monotonic() - started) * 1000)
    log.info(
        "delivered reply in %dms (text=%s chunks=%d voice=%s mood=%s)",
        elapsed,
        text_sent,
        chunks,
        voice_sent,
        turn.mood.mood.value,
    )
    return ChatOutcome(
        turn=turn,
        voice_sent=voice_sent,
        voice_reason=voice_reason,
        text_sent=text_sent,
        chunks=chunks,
        elapsed_ms=elapsed,
    )



__all__ = [
    "BudgetExceededError",
    "ChatOutcome",
    "ProviderError",
    "RateLimitedError",
    "run_turn",
]
