"""Provider request shaping, response parsing, retries and fallbacks."""

from __future__ import annotations

import json

import pytest

from leyla.ai.base import ChatMessage, ChatRequest
from leyla.ai.http import HttpJsonClient
from leyla.ai.providers.echo import EchoProvider
from leyla.ai.providers.gemini import GeminiProvider
from leyla.ai.providers.openai_compat import OpenAICompatibleProvider
from leyla.ai.registry import ProviderChain, build_chain
from leyla.errors import ProviderError

from conftest import FakeResponse


def request(json_mode: bool = True, text: str = "hello there") -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage("system", "persona"), ChatMessage("user", text)],
        model="test-model",
        temperature=0.8,
        max_tokens=200,
        json_mode=json_mode,
    )


@pytest.mark.asyncio
async def test_gemini_request_and_parse(monkeypatch):
    provider = GeminiProvider("k-123", model="gemini-test")
    captured: dict = {}

    async def fake_request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return (
            {
                "candidates": [{"content": {"parts": [{"text": '{"reply":"hi"}'}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 4},
            },
            {},
        )

    monkeypatch.setattr(provider.http, "request", fake_request)
    response = await provider.complete(request())

    assert response.text == '{"reply":"hi"}'
    assert response.usage.total == 16
    assert captured["url"].endswith("/models/gemini-test:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "k-123"
    assert "k-123" not in captured["url"]
    payload = captured["json_body"]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["systemInstruction"]["parts"][0]["text"] == "persona"
    assert payload["contents"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_gemini_handles_blocked_and_empty(monkeypatch):
    provider = GeminiProvider("k")

    async def blocked(method, url, **kwargs):
        return ({"promptFeedback": {"blockReason": "SAFETY"}}, {})

    monkeypatch.setattr(provider.http, "request", blocked)
    with pytest.raises(ProviderError):
        await provider.complete(request())

    async def empty(method, url, **kwargs):
        return ({"candidates": []}, {})

    monkeypatch.setattr(provider.http, "request", empty)
    with pytest.raises(ProviderError):
        await provider.complete(request())


@pytest.mark.asyncio
async def test_openai_compatible_request_and_parse(monkeypatch):
    provider = OpenAICompatibleProvider(
        "secret", model="m", base_url="https://api.example.test/v1", name="groq"
    )
    captured: dict = {}

    async def fake_request(method, url, **kwargs):
        captured.update({"url": url, **kwargs})
        return (
            {
                "choices": [{"message": {"content": "yo"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                "model": "m-2026",
            },
            {},
        )

    monkeypatch.setattr(provider.http, "request", fake_request)
    response = await provider.complete(request())

    assert response.text == "yo"
    assert response.model == "m-2026"
    assert captured["url"] == "https://api.example.test/v1/chat/completions"
    assert captured["json_body"]["response_format"] == {"type": "json_object"}
    assert captured["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_openai_compatible_retries_without_json_mode(monkeypatch):
    provider = OpenAICompatibleProvider("secret", model="m", base_url="https://api.example.test/v1")
    calls: list[dict] = []

    async def fake_request(method, url, **kwargs):
        calls.append(dict(kwargs["json_body"]))
        if len(calls) == 1:
            raise ProviderError(
                "response_format is not supported", provider="groq", status=400
            )
        return ({"choices": [{"message": {"content": "fallback"}}]}, {})

    monkeypatch.setattr(provider.http, "request", fake_request)
    response = await provider.complete(request())

    assert response.text == "fallback"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


@pytest.mark.asyncio
async def test_chain_falls_back_in_order():
    failing = _FailingProvider()
    working = EchoProvider()
    chain = ProviderChain([failing, working])
    response = await chain.complete(request())
    assert response.provider == "echo"
    assert chain.last_provider == "echo"
    assert chain.names == ["failing", "echo"]


@pytest.mark.asyncio
async def test_chain_raises_when_all_fail():
    chain = ProviderChain([_FailingProvider(), _FailingProvider()])
    with pytest.raises(ProviderError) as excinfo:
        await chain.complete(request())
    assert "all AI providers failed" in str(excinfo.value)


def test_build_chain_skips_fallbacks_without_keys(settings):
    settings.ai_provider = "echo"
    settings.ai_fallbacks = ("groq", "openai")
    chain = build_chain(settings, env={})
    assert chain.names == ["echo"]


@pytest.mark.asyncio
async def test_http_client_retries_then_succeeds(monkeypatch):
    responses = [
        FakeResponse(status=429, body='{"error": {"message": "slow down"}}', data=b'{"error":{}}'),
        FakeResponse(status=200, payload={"ok": True}, data=b'{"ok": true}'),
    ]

    class FakeSession:
        def request(self, *args, **kwargs):
            return responses.pop(0)

    async def fake_get_session():
        return FakeSession()

    async def no_sleep(_seconds):
        return None

    import leyla.ai.http as http_module

    monkeypatch.setattr(http_module, "get_session", fake_get_session)
    monkeypatch.setattr(http_module.asyncio, "sleep", no_sleep)

    client = HttpJsonClient("test", max_retries=2)
    payload, _ = await client.request("POST", "https://example.test")
    assert payload == {"ok": True}


@pytest.mark.asyncio
async def test_http_client_does_not_retry_client_errors(monkeypatch):
    calls = {"n": 0}

    class FakeSession:
        def request(self, *args, **kwargs):
            calls["n"] += 1
            return FakeResponse(status=400, body='{"error":{"message":"bad"}}', data=b"{}")

    async def fake_get_session():
        return FakeSession()

    import leyla.ai.http as http_module

    monkeypatch.setattr(http_module, "get_session", fake_get_session)
    client = HttpJsonClient("test", max_retries=3)
    with pytest.raises(ProviderError) as excinfo:
        await client.request("POST", "https://example.test")
    assert excinfo.value.retryable is False
    assert calls["n"] == 1


class _FailingProvider:
    name = "failing"
    supports_json_mode = True

    async def complete(self, request):
        raise ProviderError("nope", provider="failing", status=503, retryable=True)

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_echo_provider_is_json_capable():
    response = await EchoProvider().complete(request(json_mode=True))
    payload = json.loads(response.text)
    assert payload["reply"]
    assert payload["mood"]["name"]
