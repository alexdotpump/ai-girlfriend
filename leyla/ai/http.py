"""Tiny HTTP helper shared by the LLM, TTS and image providers.

It exists to centralise three things the free tiers make essential:
timeouts, bounded retries with jittered exponential backoff, and turning
provider failures into :class:`ProviderError` with a ``retryable`` flag.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import aiohttp

from ..errors import ProviderError
from ..logging_setup import get_logger

log = get_logger("leyla.http")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def get_session() -> aiohttp.ClientSession:
    """Process-wide aiohttp session (reused to keep memory and sockets low)."""
    global _session
    if _session is not None and not _session.closed:
        return _session
    async with _session_lock:
        if _session is None or _session.closed:
            _session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120, connect=15, sock_read=90),
                connector=aiohttp.TCPConnector(limit=12, ttl_dns_cache=300),
                headers={"User-Agent": "Leyla/1.0 (Discord companion)"},
            )
        return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


class HttpJsonClient:
    """JSON-over-HTTP with retries that understands provider rate limits."""

    def __init__(
        self,
        provider: str,
        *,
        max_retries: int = 2,
        base_delay: float = 1.0,
        timeout: float = 45.0,
    ) -> None:
        self.provider = provider
        self.max_retries = max(0, int(max_retries))
        self.base_delay = max(0.1, float(base_delay))
        self.timeout = float(timeout)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        data: bytes | None = None,
        timeout: float | None = None,
        expect_json: bool = True,
        acceptable_status: tuple[int, ...] = (200, 201),
    ) -> tuple[Any, dict[str, str]]:
        session = await get_session()
        attempt = 0
        last_error: Exception | None = None
        while attempt <= self.max_retries:
            attempt += 1
            try:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=timeout or self.timeout),
                ) as response:
                    body = await response.read()
                    if response.status in acceptable_status:
                        if not expect_json:
                            return body, dict(response.headers)
                        try:
                            return json.loads(body.decode("utf-8")), dict(response.headers)
                        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                            raise ProviderError(
                                f"{self.provider} returned invalid JSON: {exc}",
                                provider=self.provider,
                                status=response.status,
                                retryable=True,
                            ) from exc

                    retry_after = _retry_after_seconds(response.headers)
                    message = _extract_error(body)
                    retryable = response.status in RETRYABLE_STATUS
                    if attempt > self.max_retries or not retryable:
                        raise ProviderError(
                            f"{self.provider} HTTP {response.status}: {message}",
                            provider=self.provider,
                            status=response.status,
                            retryable=retryable,
                        )
                    delay = retry_after if retry_after is not None else self._backoff(attempt)
                    log.warning(
                        "%s returned %s, retrying in %.1fs (attempt %d/%d)",
                        self.provider,
                        response.status,
                        delay,
                        attempt,
                        self.max_retries + 1,
                    )
                    await asyncio.sleep(delay)
            except ProviderError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt > self.max_retries:
                    break
                delay = self._backoff(attempt)
                log.warning("%s network error (%s), retrying in %.1fs", self.provider, exc, delay)
                await asyncio.sleep(delay)

        raise ProviderError(
            f"{self.provider} request failed: {last_error}",
            provider=self.provider,
            retryable=True,
        )

    def _backoff(self, attempt: int) -> float:
        base = self.base_delay * (2 ** (attempt - 1))
        return base + random.uniform(0, base * 0.25)


def _retry_after_seconds(headers: Any) -> float | None:
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    try:
        return min(30.0, max(0.0, float(raw)))
    except (TypeError, ValueError):
        return None


def _extract_error(body: bytes) -> str:
    """Pull a short, secret-free error message out of a provider response."""
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return body.decode("utf-8", errors="replace")[:300]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("status") or error)[:300]
        if isinstance(error, str):
            return error[:300]
        for key in ("message", "detail", "msg"):
            if key in payload:
                return str(payload[key])[:300]
    return str(payload)[:300]
