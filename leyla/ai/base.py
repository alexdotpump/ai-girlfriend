"""Provider-agnostic chat types.

Adding a new backend means implementing :class:`ChatProvider` and registering it
in :mod:`leyla.ai.registry` — the conversation engine never imports a vendor SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str  #: "system" | "user" | "assistant"
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class ChatRequest:
    messages: list[ChatMessage]
    model: str = ""
    temperature: float = 0.9
    max_tokens: int = 320
    json_mode: bool = False
    timeout: float = 45.0
    purpose: str = "chat"

    def system_prompt(self) -> str:
        return "\n".join(m.content for m in self.messages if m.role == "system")


@dataclass(slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total,
        }


@dataclass(slots=True)
class ChatResponse:
    text: str
    provider: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0


class ChatProvider(ABC):
    """A single language-model backend."""

    name: str = "unknown"
    supports_json_mode: bool = True

    @abstractmethod
    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Return a completion for ``request`` or raise :class:`ProviderError`."""

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
