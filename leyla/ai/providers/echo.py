"""Offline provider used for smoke tests and CI.

It never touches the network. Output is deterministic-but-varied so the whole
pipeline (parsing, mood, memory, voice planning) can be exercised without keys.
It is *not* a conversational model and is documented as such.
"""

from __future__ import annotations

import hashlib
import json

from ..base import ChatProvider, ChatRequest, ChatResponse, TokenUsage

_REPLIES = (
    "mm, that's a weirdly specific thing to say and I'm kind of into it.",
    "okay wait, say that again but slower, I want to enjoy it properly.",
    "you're doing that thing where you pretend you're not thinking about it.",
    "I was going to be sensible about this, then you said that. so much for sensible.",
    "noted. filed under 'things you'll deny saying later'.",
    "come on, you can tell me the rest. I'm not going anywhere.",
)

_TAGS = ("curious", "playful", "relaxed")


class EchoProvider(ChatProvider):
    """Deterministic local provider (no API key required)."""

    name = "echo"
    supports_json_mode = True

    def __init__(self, model: str = "echo-local") -> None:
        self.model = model

    async def complete(self, request: ChatRequest) -> ChatResponse:
        last_user = next(
            (m.content for m in reversed(request.messages) if m.role == "user"), ""
        )
        digest = hashlib.sha256(f"{last_user}|{request.purpose}".encode("utf-8")).hexdigest()
        index = int(digest[:8], 16)
        reply = _REPLIES[index % len(_REPLIES)]
        tag = _TAGS[index % len(_TAGS)]

        if request.json_mode:
            payload = {
                "reply": reply,
                "mood": {"name": tag, "intensity": 0.45, "why": "offline echo provider"},
                "voice": {"send": False, "energy": "medium", "delivery": "neutral"},
                "remember": [],
            }
            text = json.dumps(payload)
        else:
            text = reply

        return ChatResponse(
            text=text,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(
                prompt_tokens=max(1, len(last_user) // 4),
                completion_tokens=len(text) // 4,
            ),
        )
