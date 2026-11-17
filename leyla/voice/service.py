"""Voice service: budgets, synthesis, caching and delivery.

Budget discipline matters: ElevenLabs' free plan is ~10k credits/month. Every
note checks a daily and a monthly character budget *before* calling the API, and
identical text is cached on disk so testing does not burn credits twice.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import discord

from ..config import Settings
from ..db import Database
from ..errors import BudgetExceededError, VoiceError
from ..logging_setup import get_logger
from ..mood.models import MoodState
from .audio import PreparedAudio, prepare_voice_note, resolve_ffmpeg
from .base import NullVoiceProvider, SpeechAudio, VoiceProvider
from .director import VoiceDirector, VoicePlan
from .sender import send_audio_attachment, send_voice_message

log = get_logger("leyla.voice.service")

CACHE_MAX_BYTES = 25 * 1024 * 1024


@dataclass(slots=True)
class VoiceResult:
    sent: bool
    reason: str = ""
    provider: str = ""
    model: str = ""
    characters: int = 0
    duration: float = 0.0
    bytes_sent: int = 0
    fallback: bool = False

    def summary(self) -> str:
        if not self.sent:
            return f"no voice note ({self.reason})"
        kind = "audio attachment" if self.fallback else "voice note"
        return f"{kind} · {self.duration:.1f}s · {self.characters} chars · {self.provider}/{self.model}"


def _periods() -> tuple[str, str]:
    now = time.gmtime()
    return time.strftime("%Y-%m-%d", now), time.strftime("%Y-%m", now)


class VoiceService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        director: VoiceDirector,
        provider: VoiceProvider,
    ) -> None:
        self.settings = settings
        self.db = db
        self.director = director
        self.provider = provider
        self._cache_dir = settings.data_dir / "voice_cache"

    @property
    def enabled(self) -> bool:
        return not isinstance(self.provider, NullVoiceProvider)

    @property
    def ffmpeg_available(self) -> bool:
        return resolve_ffmpeg(self.settings.ffmpeg_path) is not None

    async def maybe_send(
        self,
        *,
        channel: discord.abc.Messageable,
        bot: discord.Client,
        scope_id: str,
        reply: str,
        mood: MoodState,
        request: str = "auto",
        mode: str | None = None,
        frequency: float | None = None,
        energy: str = "medium",
    ) -> VoiceResult:
        """Plan, synthesise and deliver a voice note when the moment calls for it."""
        if not self.enabled:
            return VoiceResult(False, reason="voice provider is disabled")

        plan = await self.director.plan(
            scope_id=scope_id,
            reply=reply,
            mood=mood,
            request=request,
            mode=mode or self.settings.voice_mode,
            frequency=self.settings.voice_frequency if frequency is None else frequency,
            energy=energy,
            supports_tags=self.provider.supports_audio_tags,
        )
        if not plan.should_send:
            return VoiceResult(False, reason=plan.reason)

        try:
            await self._check_budget(plan.characters)
        except BudgetExceededError as exc:
            log.warning("voice budget: %s", exc)
            return VoiceResult(False, reason="budget exhausted")

        try:
            speech = await self._synthesize_cached(plan)
        except VoiceError as exc:
            log.warning("voice synthesis failed: %s", exc)
            return VoiceResult(False, reason=f"synthesis failed: {exc}")

        await self._record_usage(speech.characters or plan.characters)

        try:
            prepared = await prepare_voice_note(
                speech.data,
                source_extension=speech.extension,
                ffmpeg_path=self.settings.ffmpeg_path,
            )
        except VoiceError as exc:
            log.warning("could not prepare voice note: %s", exc)
            # Degrade to a normal attachment so the audio is still playable.
            try:
                await send_audio_attachment(
                    channel,
                    speech.data,
                    filename=f"leyla-{int(time.time())}.{speech.extension}",
                )
                await self.director.mark_sent(scope_id, plan)
                return VoiceResult(
                    sent=True,
                    reason=f"sent as attachment ({exc})",
                    provider=speech.provider,
                    model=speech.model,
                    characters=speech.characters,
                    bytes_sent=len(speech.data),
                    fallback=True,
                )
            except (discord.HTTPException, VoiceError) as fallback_exc:
                log.error("audio attachment fallback failed: %s", fallback_exc)
                return VoiceResult(False, reason="delivery failed")

        try:
            await send_voice_message(channel, bot, prepared)
        except VoiceError as exc:
            log.error("voice message delivery failed: %s", exc)
            return VoiceResult(False, reason=f"delivery failed: {exc}")

        await self.director.mark_sent(scope_id, plan)
        log.info(
            "voice sent: mood=%s tags=%s chars=%s",
            mood.mood.value,
            ",".join(plan.tags) or "none",
            speech.characters,
        )
        return VoiceResult(
            sent=True,
            reason=plan.reason,
            provider=speech.provider,
            model=speech.model,
            characters=speech.characters,
            duration=prepared.duration,
            bytes_sent=len(prepared.ogg),
        )

    async def synthesize_now(self, text: str) -> SpeechAudio:
        """Synthesise without delivery (used by tests and /voice preview)."""
        plan = VoicePlan(should_send=True, text=text, spoken_text=text)
        return await self._synthesize_cached(plan)

    async def prepare_test_note(self, text: str) -> PreparedAudio:
        speech = await self.synthesize_now(text)
        return await prepare_voice_note(
            speech.data,
            source_extension=speech.extension,
            ffmpeg_path=self.settings.ffmpeg_path,
        )

    # ------------------------------------------------------------- internals
    async def _synthesize_cached(self, plan: VoicePlan) -> SpeechAudio:
        key_text = f"{self.provider.name}|{self.settings.elevenlabs_model}|{plan.text}"
        digest = hashlib.sha256(key_text.encode("utf-8")).hexdigest()[:32]
        cached = self._load_cache(digest)
        if cached is not None:
            log.info("voice cache hit (%s chars)", len(plan.spoken_text))
            return cached

        speech = await self.provider.synthesize(plan.text)
        self._save_cache(digest, speech)
        return speech

    def _cache_path(self, digest: str) -> Path:
        return self._cache_dir / f"{digest}.audio"

    def _load_cache(self, digest: str) -> SpeechAudio | None:
        path = self._cache_path(digest)
        meta_path = path.with_suffix(".json")
        if not path.is_file() or not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            return SpeechAudio(
                data=path.read_bytes(),
                mime=meta.get("mime", "audio/mpeg"),
                extension=meta.get("extension", "mp3"),
                provider=meta.get("provider", self.provider.name),
                model=meta.get("model", ""),
                characters=int(meta.get("characters", 0)),
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _save_cache(self, digest: str, speech: SpeechAudio) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(digest).write_bytes(speech.data)
            self._cache_path(digest).with_suffix(".json").write_text(
                json.dumps(
                    {
                        "mime": speech.mime,
                        "extension": speech.extension,
                        "provider": speech.provider,
                        "model": speech.model,
                        "characters": speech.characters,
                    }
                ),
                encoding="utf-8",
            )
            self._prune_cache()
        except OSError as exc:  # pragma: no cover - disk full/permissions
            log.warning("could not cache audio: %s", exc)

    def _prune_cache(self) -> None:
        files = sorted(
            (p for p in self._cache_dir.glob("*.audio")), key=lambda p: p.stat().st_mtime
        )
        total = sum(p.stat().st_size for p in files)
        while files and total > CACHE_MAX_BYTES:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
            oldest.with_suffix(".json").unlink(missing_ok=True)

    async def _check_budget(self, characters: int) -> None:
        if characters <= 0:
            return
        day, month = _periods()
        daily_limit = self.settings.tts_daily_char_limit
        monthly_limit = self.settings.tts_monthly_char_limit
        if daily_limit:
            used_today = await self.db.get_usage(day, "tts_chars")
            if used_today + characters > daily_limit:
                raise BudgetExceededError(
                    f"daily TTS budget would be exceeded ({used_today}+{characters}/{daily_limit})"
                )
        if monthly_limit:
            used_month = await self.db.get_usage(month, "tts_chars")
            if used_month + characters > monthly_limit:
                raise BudgetExceededError(
                    f"monthly TTS budget would be exceeded ({used_month}+{characters}/{monthly_limit})"
                )

    async def _record_usage(self, characters: int) -> None:
        if characters <= 0:
            return
        day, month = _periods()
        await self.db.increment_usage(day, "tts_chars", characters)
        await self.db.increment_usage(month, "tts_chars", characters)

    async def usage_report(self) -> dict[str, int]:
        day, month = _periods()
        return {
            "today": await self.db.get_usage(day, "tts_chars"),
            "month": await self.db.get_usage(month, "tts_chars"),
            "daily_limit": self.settings.tts_daily_char_limit,
            "monthly_limit": self.settings.tts_monthly_char_limit,
        }
