"""Google Gemini (AI Studio) provider.

The free tier of the Gemini API is the cheapest capable option we verified:
``gemini-flash-latest`` (currently Gemini 3.x Flash) is listed as free of charge
and needs no billing account. The key is sent in the ``x-goog-api-key`` header
so it never appears in request URLs or error text.
"""

from __future__ import annotations

from ..base import ChatProvider, ChatRequest, ChatResponse, TokenUsage
from ..http import HttpJsonClient
from ...errors import ProviderError

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(ChatProvider):
    name = "gemini"
    supports_json_mode = True

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-flash-latest",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 45.0,
        max_retries: int = 2,
        fallback_models: tuple[str, ...] = (),
    ) -> None:
        if not api_key:
            raise ProviderError("Gemini API key missing", provider=self.name)
        self.api_key = api_key
        self.model = model or "gemini-flash-latest"
        self.fallback_models = tuple(m for m in fallback_models if m != self.model)
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.http = HttpJsonClient(self.name, max_retries=max_retries, timeout=timeout)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        system = request.system_prompt()
        contents = [
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
            for message in request.messages
            if message.role != "system"
        ]
        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if request.json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        # Free Flash models regularly answer 503 "high demand". A second model
        # ID is a better use of the turn than retrying the busy one.
        last_error: ProviderError | None = None
        for model in (self.model, *self.fallback_models):
            url = f"{self.base_url}/models/{model}:generateContent"
            try:
                data, _ = await self.http.request(
                    "POST",
                    url,
                    headers=headers,
                    json_body=payload,
                    timeout=request.timeout,
                )
                self.model = model
                break
            except ProviderError as exc:
                last_error = exc
                # A busy, retired or unreachable model should hand the turn to
                # the next model ID instead of failing the conversation.
                if exc.status in {404, 429, 503} or exc.retryable:
                    continue
                raise
        else:
            raise last_error or ProviderError("Gemini unavailable", provider=self.name)

        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            raise ProviderError(f"Gemini blocked the prompt ({blocked})", provider=self.name, status=400)

        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError("Gemini returned no candidates", provider=self.name)
        candidate = candidates[0]
        if candidate.get("finishReason") in {"SAFETY", "PROHIBITED_CONTENT"}:
            raise ProviderError(
                f"Gemini refused the response ({candidate.get('finishReason')})",
                provider=self.name,
                status=400,
            )
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise ProviderError("Gemini returned an empty response", provider=self.name)

        usage_meta = data.get("usageMetadata") or {}
        usage = TokenUsage(
            prompt_tokens=int(usage_meta.get("promptTokenCount") or 0),
            completion_tokens=int(usage_meta.get("candidatesTokenCount") or 0),
        )
        return ChatResponse(text=text, provider=self.name, model=self.model, usage=usage)
