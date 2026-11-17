"""Provider construction, fallback chain and configuration reporting."""

from __future__ import annotations

import os

from ..config import PROVIDER_BASE_URLS, Settings
from ..errors import ProviderError
from ..logging_setup import get_logger
from .base import ChatProvider, ChatRequest, ChatResponse
from .providers.echo import EchoProvider
from .providers.gemini import GeminiProvider
from .providers.openai_compat import OpenAICompatibleProvider

log = get_logger("leyla.ai.registry")


def build_provider(name: str, settings: Settings) -> ChatProvider:
    """Instantiate a provider by name using the current settings."""
    name = (name or "").strip().lower()
    if name == "echo":
        return EchoProvider(settings.ai_model or "echo-local")

    api_key = settings.ai_key_for(name)
    if not api_key:
        raise ProviderError(f"no API key configured for provider {name}", provider=name)

    if name == "gemini":
        return GeminiProvider(
            api_key,
            model=settings.ai_model,
            base_url=settings.ai_base_url or PROVIDER_BASE_URLS["gemini"],
            timeout=settings.ai_timeout_seconds,
            max_retries=settings.ai_max_retries,
        )

    if name in {"groq", "openai", "openrouter", "custom"}:
        base_url = (
            settings.ai_base_url
            if name == settings.ai_provider
            else PROVIDER_BASE_URLS.get(name, "")
        )
        model = settings.ai_model if name == settings.ai_provider else ""
        if not model:
            fallback_models = {
                "groq": "openai/gpt-oss-120b",
                "openai": "gpt-4.1-mini",
                "openrouter": "openrouter/auto",
            }
            model = fallback_models.get(name, "")
        if not model:
            raise ProviderError(f"no model configured for provider {name}", provider=name)
        extra_headers = {}
        if name == "openrouter":
            extra_headers = {
                "HTTP-Referer": "https://github.com/alexdotpump/ai-girlfriend",
                "X-Title": "Leyla",
            }
        return OpenAICompatibleProvider(
            api_key,
            model=model,
            base_url=base_url,
            name=name,
            extra_headers=extra_headers,
            timeout=settings.ai_timeout_seconds,
            max_retries=settings.ai_max_retries,
        )

    raise ProviderError(f"unknown AI provider {name!r}", provider=name)


class ProviderChain:
    """Tries the primary provider, then any configured fallbacks."""

    def __init__(self, providers: list[ChatProvider]) -> None:
        if not providers:
            raise ProviderError("no AI providers configured", provider="chain")
        self.providers = providers
        self.last_provider: str | None = None

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.providers]

    @property
    def primary(self) -> ChatProvider:
        return self.providers[0]

    async def complete(self, request: ChatRequest) -> ChatResponse:
        errors: list[str] = []
        for provider in self.providers:
            try:
                response = await provider.complete(request)
                self.last_provider = provider.name
                return response
            except ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
                log.warning("provider %s failed: %s", provider.name, exc)
                continue
        raise ProviderError("all AI providers failed — " + " | ".join(errors), provider="chain")

    async def aclose(self) -> None:
        for provider in self.providers:
            try:
                await provider.aclose()
            except Exception:  # noqa: BLE001 - shutdown is best effort
                log.debug("failed closing provider %s", provider.name, exc_info=True)


def build_chain(settings: Settings, env: dict[str, str] | None = None) -> ProviderChain:
    """Build the primary provider plus configured fallbacks.

    Fallbacks are skipped (with a log line) when their key is absent, so a
    single key still works perfectly.
    """
    env = dict(os.environ if env is None else env)
    providers: list[ChatProvider] = []
    for name in (settings.ai_provider, *settings.ai_fallbacks):
        if any(p.name == name for p in providers):
            continue
        if name != settings.ai_provider and not settings.ai_key_for(name, env):
            log.info("skipping fallback provider %s (no key)", name)
            continue
        try:
            providers.append(build_provider(name, settings))
        except ProviderError as exc:
            log.warning("could not initialise provider %s: %s", name, exc)
    if not providers:
        # Last-resort offline provider so the bot still starts and explains itself.
        log.error("no usable AI provider configured; falling back to offline echo provider")
        providers.append(EchoProvider())
    return ProviderChain(providers)


def describe_chain(chain: ProviderChain) -> str:
    return " → ".join(chain.names)
