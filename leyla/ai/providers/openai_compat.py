"""OpenAI-compatible chat provider.

Covers Groq, OpenAI, OpenRouter and any self-hosted/OpenAI-style endpoint —
only ``base_url``/``model``/key change. This is what makes swapping the model a
configuration change instead of a rewrite.
"""

from __future__ import annotations

from ..base import ChatProvider, ChatRequest, ChatResponse, TokenUsage
from ..http import HttpJsonClient
from ...errors import ProviderError


class OpenAICompatibleProvider(ChatProvider):
    name = "openai-compatible"
    supports_json_mode = True

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        base_url: str,
        name: str = "openai-compatible",
        extra_headers: dict[str, str] | None = None,
        timeout: float = 45.0,
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise ProviderError(f"{name} API key missing", provider=name)
        if not base_url:
            raise ProviderError(f"{name} base URL missing", provider=name)
        self.name = name
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.extra_headers = extra_headers or {}
        self.http = HttpJsonClient(name, max_retries=max_retries, timeout=timeout)
        # Providers such as Groq reject unknown fields; we degrade gracefully.
        self.supports_json_mode = True

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    async def complete(self, request: ChatRequest) -> ChatResponse:
        model = request.model or self.model
        payload: dict = {
            "model": model,
            "messages": [message.as_dict() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_mode and self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.base_url}/chat/completions"
        try:
            data, _ = await self.http.request(
                "POST",
                url,
                headers=self._headers(),
                json_body=payload,
                timeout=request.timeout,
                # Some gateways answer 200 with a partial body; treat 2xx as success.
                acceptable_status=tuple(range(200, 300)),
            )
        except ProviderError as exc:
            if request.json_mode and exc.status == 400 and "response_format" in str(exc).lower():
                # Retry once without JSON mode; the parser handles plain text.
                payload.pop("response_format", None)
                data, _ = await self.http.request(
                    "POST",
                    url,
                    headers=self._headers(),
                    json_body=payload,
                    timeout=request.timeout,
                    acceptable_status=tuple(range(200, 300)),
                )
            else:
                raise

        choices = data.get("choices") or []
        if not choices:  # pragma: no cover - defensive
            raise ProviderError(f"{self.name} returned no choices", provider=self.name)
        message = choices[0].get("message") or {}
        text = (message.get("content") or "").strip()
        if not text:
            raise ProviderError(f"{self.name} returned an empty response", provider=self.name)

        usage_raw = data.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
        )
        return ChatResponse(
            text=text,
            provider=self.name,
            model=str(data.get("model") or model),
            usage=usage,
        )
