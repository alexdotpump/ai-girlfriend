"""Exception types used across Leyla.

Every error carries a *safe* public message: the Discord layer logs the details
and only ever shows the sanitised text to the user.
"""

from __future__ import annotations


class LeylaError(Exception):
    """Base class for all project errors."""

    public_message = "Something went wrong on my side."


class ConfigError(LeylaError):
    """Configuration is missing or invalid."""

    public_message = "I'm not configured correctly yet."


class ProviderError(LeylaError):
    """An external API (LLM, TTS, images) failed."""

    public_message = "My thoughts got stuck for a second — try again?"

    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.retryable = retryable

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = super().__str__()
        return f"[{self.provider}{f' {self.status}' if self.status else ''}] {base}"


class RateLimitedError(LeylaError):
    """The owner (or the global budget) hit a usage limit."""

    public_message = "Slow down a little — I need to breathe between messages."

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class BudgetExceededError(LeylaError):
    """A configured daily/monthly budget would be exceeded."""

    public_message = "I'm at my spending limit for today. Let's talk tomorrow?"


class VoiceError(LeylaError):
    """Voice synthesis or audio preparation failed."""

    public_message = "My voice is acting up — I'll answer in text instead."


class ImageError(LeylaError):
    """Character image lookup or generation failed."""

    public_message = "I couldn't get that picture ready."


class UnauthorizedError(LeylaError):
    """A non-owner tried to use private functionality."""

    public_message = "This companion is private."
