"""Typed, validated application configuration.

All secrets come from the environment (KataBump supplies a ``.env`` file at the
project root). Nothing here is ever written to logs — use
:func:`leyla.security.redact` if a value must appear in a log line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .errors import ConfigError

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}

#: Sensible, *currently free or cheap* default model per provider. These are
#: defaults only — AI_MODEL always wins, and the model can be swapped without
#: touching code (that is the point of the provider abstraction).
PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "gemini": "gemini-flash-latest",
    "groq": "openai/gpt-oss-120b",
    "openai": "gpt-4.1-mini",
    "openrouter": "openrouter/auto",
    "custom": "",
    "echo": "echo-local",
}

#: Environment variable consulted for each provider's key, in order.
PROVIDER_KEY_ENVS: dict[str, tuple[str, ...]] = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AI_API_KEY"),
    "groq": ("GROQ_API_KEY", "AI_API_KEY"),
    "openai": ("OPENAI_API_KEY", "AI_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY", "AI_API_KEY"),
    "custom": ("AI_API_KEY",),
    "echo": (),
}

PROVIDER_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

AI_PROVIDERS = ("gemini", "groq", "openai", "openrouter", "custom", "echo")
VOICE_PROVIDERS = ("groq", "elevenlabs", "openai", "edge", "none")
VOICE_MODES = ("off", "request", "occasional", "always")
IMAGE_PROVIDERS = ("none", "pollinations", "openai")


def _bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{key} must be a boolean (true/false), got {raw!r}")


def _int(env: Mapping[str, str], key: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(float(raw.strip()))
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if lo is not None and value < lo:
        raise ConfigError(f"{key} must be >= {lo}, got {value}")
    if hi is not None and value > hi:
        raise ConfigError(f"{key} must be <= {hi}, got {value}")
    return value


def _float(env: Mapping[str, str], key: str, default: float, lo: float = 0.0, hi: float = 1.0) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo} and {hi}, got {value}")
    return value


def _int_set(env: Mapping[str, str], key: str) -> frozenset[int]:
    raw = env.get(key, "")
    values: set[int] = set()
    for chunk in raw.replace(";", ",").replace(" ", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            values.add(int(chunk))
        except ValueError as exc:
            raise ConfigError(f"{key} contains a non-numeric Discord ID: {chunk!r}") from exc
    return frozenset(values)


def _csv(env: Mapping[str, str], key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(slots=True)
class Settings:
    """Everything the application can be configured with."""

    # --- Discord -----------------------------------------------------------
    discord_token: str = ""
    owner_ids: frozenset[int] = frozenset()
    guild_id: int | None = None
    allowed_channel_ids: frozenset[int] = frozenset()
    bot_name: str = "Leyla"

    # --- Storage -----------------------------------------------------------
    data_dir: Path = Path("data")
    db_path: Path = Path("data/leyla.sqlite3")

    # --- AI ----------------------------------------------------------------
    ai_provider: str = "gemini"
    ai_model: str = ""
    #: Extra model IDs tried when the primary model is overloaded (503) or gone.
    ai_model_fallbacks: tuple[str, ...] = ()
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_fallbacks: tuple[str, ...] = ()
    ai_temperature: float = 0.9
    ai_max_output_tokens: int = 320
    ai_timeout_seconds: int = 45
    ai_max_retries: int = 2
    ai_daily_request_limit: int = 400
    chat_history_turns: int = 14
    chat_max_message_chars: int = 1900
    memory_extraction_enabled: bool = True
    memory_max_per_scope: int = 400
    rate_limit_per_minute: int = 12

    # --- Voice -------------------------------------------------------------
    voice_provider: str = "groq"
    voice_mode: str = "occasional"
    voice_frequency: float = 0.15
    voice_max_chars: int = 190
    tts_daily_char_limit: int = 2000
    tts_monthly_char_limit: int = 8000
    voice_cooldown_minutes: int = 20
    nonverbal_enabled: bool = True
    ffmpeg_path: str = ""

    groq_api_key: str = ""
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "autumn"
    groq_tts_url: str = "https://api.groq.com/openai/v1/audio/speech"

    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model: str = "eleven_v4"
    elevenlabs_model_fallbacks: tuple[str, ...] = ("eleven_v3", "eleven_multilingual_v2")
    elevenlabs_stability: float = 0.45
    elevenlabs_similarity: float = 0.8
    elevenlabs_style: float = 0.35
    elevenlabs_speaker_boost: bool = True

    openai_api_key: str = ""
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "sage"
    openai_tts_instructions: str = (
        "Warm, young adult female voice, relaxed conversational delivery, "
        "soft natural breathing, light playful tone, never robotic or announcer-like."
    )
    edge_tts_voice: str = "en-US-AvaMultilingualNeural"

    # --- Images ------------------------------------------------------------
    image_provider: str = "none"
    image_generation_enabled: bool = False
    image_daily_limit: int = 4
    image_size: str = "1024x1024"
    openai_image_model: str = "gpt-image-1"

    # --- Ops ---------------------------------------------------------------
    log_level: str = "INFO"
    log_file: str = ""
    environment: str = "production"

    #: Non-fatal notes gathered while loading (shown as warnings at startup).
    load_warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        warnings: list[str] = []

        def get(key: str, default: str = "") -> str:
            return (env.get(key) or default).strip()

        provider = get("AI_PROVIDER", "gemini").lower()
        if provider not in AI_PROVIDERS:
            raise ConfigError(
                f"AI_PROVIDER must be one of {', '.join(AI_PROVIDERS)} (got {provider!r})"
            )

        api_key = ""
        for key_env in PROVIDER_KEY_ENVS[provider]:
            if get(key_env):
                api_key = get(key_env)
                break

        base_url = get("AI_BASE_URL") or PROVIDER_BASE_URLS.get(provider, "")
        model = get("AI_MODEL") or PROVIDER_DEFAULT_MODELS.get(provider, "")
        if provider == "custom" and not base_url:
            raise ConfigError("AI_PROVIDER=custom requires AI_BASE_URL")

        voice_provider = get("VOICE_PROVIDER", "groq").lower()
        if voice_provider not in VOICE_PROVIDERS:
            raise ConfigError(
                f"VOICE_PROVIDER must be one of {', '.join(VOICE_PROVIDERS)} (got {voice_provider!r})"
            )

        voice_mode = get("VOICE_MODE", "occasional").lower()
        # Friendly aliases so the value matches the user-facing preference names.
        voice_mode = {"on_request": "request", "ask": "request", "auto": "occasional"}.get(
            voice_mode, voice_mode
        )
        if voice_mode not in VOICE_MODES:
            raise ConfigError(
                f"VOICE_MODE must be one of {', '.join(VOICE_MODES)} (got {voice_mode!r})"
            )

        image_provider = get("IMAGE_PROVIDER", "none").lower()
        if image_provider not in IMAGE_PROVIDERS:
            raise ConfigError(
                f"IMAGE_PROVIDER must be one of {', '.join(IMAGE_PROVIDERS)} (got {image_provider!r})"
            )

        data_dir = Path(get("DATA_DIR", "data")).expanduser()
        db_path = Path(get("DB_PATH", str(data_dir / "leyla.sqlite3"))).expanduser()

        settings = cls(
            discord_token=get("DISCORD_TOKEN"),
            owner_ids=_int_set(env, "OWNER_DISCORD_ID") | _int_set(env, "OWNER_DISCORD_IDS"),
            guild_id=_int(env, "DISCORD_GUILD_ID", 0) or None,
            allowed_channel_ids=_int_set(env, "ALLOWED_CHANNEL_IDS"),
            bot_name=get("BOT_NAME", "Leyla"),
            data_dir=data_dir,
            db_path=db_path,
            ai_provider=provider,
            ai_model=model,
            ai_model_fallbacks=_csv(
                env,
                "AI_MODEL_FALLBACKS",
                ("gemini-3.5-flash", "gemini-flash-lite-latest")
                if provider == "gemini"
                else (),
            ),
            ai_api_key=api_key,
            ai_base_url=base_url.rstrip("/"),
            ai_fallbacks=tuple(
                p for p in _csv(env, "AI_FALLBACKS") if p in AI_PROVIDERS and p != provider
            ),
            ai_temperature=_float(env, "AI_TEMPERATURE", 0.9, 0.0, 2.0),
            ai_max_output_tokens=_int(env, "AI_MAX_OUTPUT_TOKENS", 320, 64, 4000),
            ai_timeout_seconds=_int(env, "AI_TIMEOUT_SECONDS", 45, 5, 180),
            ai_max_retries=_int(env, "AI_MAX_RETRIES", 2, 0, 5),
            ai_daily_request_limit=_int(env, "AI_DAILY_REQUEST_LIMIT", 400, 1, 100000),
            chat_history_turns=_int(env, "CHAT_HISTORY_TURNS", 14, 2, 60),
            chat_max_message_chars=_int(env, "CHAT_MAX_MESSAGE_CHARS", 1900, 200, 2000),
            memory_extraction_enabled=_bool(env, "MEMORY_EXTRACTION_ENABLED", True),
            memory_max_per_scope=_int(env, "MEMORY_MAX_PER_SCOPE", 400, 10, 10000),
            rate_limit_per_minute=_int(env, "RATE_LIMIT_PER_MINUTE", 12, 1, 120),
            voice_provider=voice_provider,
            voice_mode=voice_mode,
            voice_frequency=_float(env, "VOICE_FREQUENCY", 0.15, 0.0, 1.0),
            voice_max_chars=_int(env, "VOICE_MAX_CHARS", 190, 80, 2000),
            tts_daily_char_limit=_int(env, "TTS_DAILY_CHAR_LIMIT", 2000, 0, 100000),
            tts_monthly_char_limit=_int(env, "TTS_MONTHLY_CHAR_LIMIT", 8000, 0, 1000000),
            voice_cooldown_minutes=_int(env, "VOICE_COOLDOWN_MINUTES", 20, 0, 1440),
            nonverbal_enabled=_bool(env, "VOICE_NONVERBAL_ENABLED", True),
            ffmpeg_path=get("FFMPEG_PATH"),
            groq_api_key=get("GROQ_API_KEY"),
            groq_tts_model=get("GROQ_TTS_MODEL", "canopylabs/orpheus-v1-english"),
            groq_tts_voice=get("GROQ_TTS_VOICE", "autumn"),
            groq_tts_url=get(
                "GROQ_TTS_URL", "https://api.groq.com/openai/v1/audio/speech"
            ),
            elevenlabs_api_key=get("ELEVENLABS_API_KEY"),
            elevenlabs_voice_id=get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
            elevenlabs_model=get("ELEVENLABS_MODEL", "eleven_v4"),
            elevenlabs_model_fallbacks=_csv(
                env, "ELEVENLABS_MODEL_FALLBACKS", ("eleven_v3", "eleven_multilingual_v2")
            ),
            elevenlabs_stability=_float(env, "ELEVENLABS_STABILITY", 0.45),
            elevenlabs_similarity=_float(env, "ELEVENLABS_SIMILARITY", 0.8),
            elevenlabs_style=_float(env, "ELEVENLABS_STYLE", 0.35),
            elevenlabs_speaker_boost=_bool(env, "ELEVENLABS_SPEAKER_BOOST", True),
            openai_api_key=get("OPENAI_API_KEY"),
            openai_tts_model=get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            openai_tts_voice=get("OPENAI_TTS_VOICE", "sage"),
            openai_tts_instructions=(
                get("OPENAI_TTS_INSTRUCTIONS")
                or "Warm, young adult female voice, relaxed conversational delivery, soft natural "
                "breathing, light playful tone, never robotic or announcer-like."
            ),
            edge_tts_voice=get("EDGE_TTS_VOICE", "en-US-AvaMultilingualNeural"),
            image_provider=image_provider,
            image_generation_enabled=_bool(env, "IMAGE_GENERATION_ENABLED", False),
            image_daily_limit=_int(env, "IMAGE_DAILY_LIMIT", 4, 0, 100),
            image_size=get("IMAGE_SIZE", "1024x1024"),
            openai_image_model=get("OPENAI_IMAGE_MODEL", "gpt-image-1"),
            log_level=get("LOG_LEVEL", "INFO").upper(),
            log_file=get("LOG_FILE"),
            environment=get("ENVIRONMENT", "production"),
        )

        # TTS keys are separate from the chat key: a voice provider can reuse the
        # OpenAI key when both features use OpenAI.
        if settings.voice_provider == "openai" and not settings.openai_api_key:
            settings.openai_api_key = get("OPENAI_API_KEY") or settings.ai_api_key
        # The Groq key doubles as the TTS key; AI_API_KEY alone is not enough
        # because it might belong to a different vendor.
        if settings.voice_provider == "groq" and not settings.groq_api_key:
            settings.groq_api_key = settings.ai_api_key if provider == "groq" else ""

        settings.load_warnings = warnings
        return settings

    # -------------------------------------------------------------- helpers
    @property
    def is_owner_only(self) -> bool:
        return True

    def ai_key_for(self, provider: str, env: Mapping[str, str] | None = None) -> str:
        """Resolve a provider key without lending the primary key to fallbacks.

        ``AI_API_KEY`` is generic, so it is only honoured for the configured
        primary provider — otherwise a Gemini key could be sent to Groq.
        """
        env = os.environ if env is None else env
        if provider == self.ai_provider:
            if self.ai_api_key:
                return self.ai_api_key
        for key_env in PROVIDER_KEY_ENVS.get(provider, ()):
            if key_env == "AI_API_KEY" and provider != self.ai_provider:
                continue
            value = (env.get(key_env) or "").strip()
            if value:
                return value
        return ""

    def tts_api_key(self) -> str:
        if self.voice_provider == "groq":
            return self.groq_api_key
        if self.voice_provider == "elevenlabs":
            return self.elevenlabs_api_key
        if self.voice_provider == "openai":
            return self.openai_api_key or self.ai_api_key
        return ""

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def problems(self) -> list[str]:
        """Blocking problems that must be fixed before the bot can run."""
        problems: list[str] = []
        if not self.discord_token:
            problems.append("DISCORD_TOKEN is required")
        if not self.owner_ids:
            problems.append(
                "OWNER_DISCORD_ID is required (your Discord user ID; right-click yourself → Copy User ID)"
            )
        if self.ai_provider != "echo" and not self.ai_api_key:
            key_env = PROVIDER_KEY_ENVS[self.ai_provider][0]
            problems.append(
                f"{key_env} is required for AI_PROVIDER={self.ai_provider} "
                "(or set AI_PROVIDER=echo for an offline smoke test)"
            )
        if self.ai_provider != "echo" and not self.ai_model:
            problems.append("AI_MODEL is required for the selected AI_PROVIDER")
        if self.voice_mode != "off" and self.voice_provider == "elevenlabs":
            if not self.elevenlabs_api_key:
                problems.append(
                    "ELEVENLABS_API_KEY is required when VOICE_PROVIDER=elevenlabs "
                    "(set VOICE_MODE=off to run text-only)"
                )
            if not self.elevenlabs_voice_id:
                problems.append("ELEVENLABS_VOICE_ID is required when VOICE_PROVIDER=elevenlabs")
        if self.voice_mode != "off" and self.voice_provider == "groq":
            if not self.groq_api_key:
                problems.append(
                    "GROQ_API_KEY is required when VOICE_PROVIDER=groq "
                    "(set VOICE_MODE=off to run text-only)"
                )
        if self.voice_mode != "off" and self.voice_provider == "openai":
            if not (self.openai_api_key or self.ai_api_key):
                problems.append("OPENAI_API_KEY is required when VOICE_PROVIDER=openai")
        if self.voice_mode != "off" and self.voice_provider == "edge":
            problems.append(
                "VOICE_PROVIDER=edge needs the optional edge-tts package "
                "(pip install edge-tts) — or use elevenlabs/openai"
            )
        if self.image_generation_enabled and self.image_provider == "openai":
            if not (self.openai_api_key or self.ai_api_key):
                problems.append("IMAGE_PROVIDER=openai needs OPENAI_API_KEY")
        return problems

    def warnings(self) -> list[str]:
        notes = list(self.load_warnings)
        if not self.guild_id:
            notes.append(
                "DISCORD_GUILD_ID is unset: commands will be synced globally, which can take up to an hour"
            )
        if self.voice_mode == "always":
            notes.append(
                "VOICE_MODE=always sends a voice note for every reply and can burn your TTS credits quickly"
            )
        if self.voice_provider == "groq" and self.voice_mode != "off":
            notes.append(
                "voice notes use Groq Orpheus (wav → ffmpeg → Ogg/Opus, 200 character input cap)"
            )
        if self.voice_provider == "elevenlabs" and self.voice_mode != "off":
            if self.elevenlabs_model in {"eleven_v3", "eleven_v4"}:
                notes.append(
                    f"using {self.elevenlabs_model} for expressive audio tags; if your plan rejects it, "
                    "Leyla falls back through ELEVENLABS_MODEL_FALLBACKS"
                )
        if not self.image_generation_enabled:
            notes.append("image generation is disabled (set IMAGE_GENERATION_ENABLED=true to opt in)")
        return notes
