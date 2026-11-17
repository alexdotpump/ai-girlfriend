"""Service container: builds and owns every component exactly once."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .ai.engine import ConversationEngine
from .ai.http import close_session
from .ai.registry import ProviderChain, build_chain
from .config import Settings
from .db import Database
from .images.assets import CharacterAssets
from .images.service import ImageService
from .logging_setup import get_logger
from .memory.context import ConversationStore
from .memory.extractor import MemoryExtractor
from .memory.store import MemoryStore
from .mood.engine import MoodEngine
from .preferences import PreferenceStore
from .runtime_config import RuntimeConfig
from .security import SlidingWindowLimiter
from .voice.director import VoiceDirector
from .voice.providers.registry import build_voice_provider
from .voice.service import VoiceService

log = get_logger("leyla.services")


@dataclass(slots=True)
class Services:
    settings: Settings
    db: Database
    chain: ProviderChain
    memories: MemoryStore
    conversations: ConversationStore
    mood: MoodEngine
    engine: ConversationEngine
    voice: VoiceService
    images: ImageService
    preferences: PreferenceStore
    runtime: RuntimeConfig
    limiter: SlidingWindowLimiter
    assets: CharacterAssets
    started_at: float = field(default_factory=time.time)
    _closed: bool = False

    @classmethod
    async def create(cls, settings: Settings) -> "Services":
        settings.ensure_dirs()
        db = Database(settings.db_path)
        db.connect()

        chain = build_chain(settings)
        memories = MemoryStore(db, max_per_scope=settings.memory_max_per_scope)
        conversations = ConversationStore(db)
        mood = MoodEngine(db)
        extractor = MemoryExtractor(
            chain,
            model=settings.ai_model,
            timeout=float(min(30, settings.ai_timeout_seconds)),
        )
        engine = ConversationEngine(settings, chain, db, memories, conversations, mood, extractor)

        director = VoiceDirector(
            db,
            max_chars=settings.voice_max_chars,
            cooldown_minutes=settings.voice_cooldown_minutes,
            nonverbal_enabled=settings.nonverbal_enabled,
        )
        voice_provider = build_voice_provider(settings)
        voice = VoiceService(settings, db, director, voice_provider)

        assets = CharacterAssets()
        images = ImageService(settings, db, assets=assets)
        if assets.missing_identity():
            log.warning(
                "no character portrait found in assets/character — /photo will be unavailable "
                "until you add leyla_default.png"
            )

        services = cls(
            settings=settings,
            db=db,
            chain=chain,
            memories=memories,
            conversations=conversations,
            mood=mood,
            engine=engine,
            voice=voice,
            images=images,
            preferences=PreferenceStore(db, settings),
            runtime=RuntimeConfig(db),
            limiter=SlidingWindowLimiter(settings.rate_limit_per_minute, window_seconds=60),
            assets=assets,
        )
        log.info(
            "services ready | ai=%s | voice=%s (%s) | ffmpeg=%s | images=%s",
            " → ".join(chain.names),
            settings.voice_provider,
            settings.voice_mode,
            "yes" if voice.ffmpeg_available else "MISSING (voice notes need ffmpeg)",
            "on" if images.generation_enabled else "off",
        )
        return services

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.chain.aclose()
        except Exception:  # noqa: BLE001 - shutdown best effort
            log.debug("provider close failed", exc_info=True)
        try:
            await self.voice.provider.aclose()
        except Exception:  # noqa: BLE001
            log.debug("voice provider close failed", exc_info=True)
        try:
            await close_session()
        except Exception:  # noqa: BLE001
            log.debug("http session close failed", exc_info=True)
        self.db.close()

    # ----------------------------------------------------------- diagnostics
    async def usage_snapshot(self) -> dict[str, int]:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        month = time.strftime("%Y-%m", time.gmtime())
        return {
            "llm_today": await self.db.get_usage(day, "llm"),
            "llm_limit": self.settings.ai_daily_request_limit,
            "tts_today": await self.db.get_usage(day, "tts_chars"),
            "tts_month": await self.db.get_usage(month, "tts_chars"),
            "tts_daily_limit": self.settings.tts_daily_char_limit,
            "tts_monthly_limit": self.settings.tts_monthly_char_limit,
            "images_today": await self.db.get_usage(day, "images"),
            "images_limit": self.settings.image_daily_limit,
        }
