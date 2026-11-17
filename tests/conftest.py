"""Shared pytest fixtures and fakes."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from leyla.ai.base import ChatProvider, ChatRequest, ChatResponse, TokenUsage  # noqa: E402
from leyla.config import Settings  # noqa: E402
from leyla.db import Database  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    env = {
        "DISCORD_TOKEN": "test-token-not-real",
        "OWNER_DISCORD_ID": "42",
        "AI_PROVIDER": "echo",
        "AI_MODEL": "echo-local",
        "VOICE_PROVIDER": "none",
        "VOICE_MODE": "off",
        "DATA_DIR": str(tmp_path / "data"),
        "DB_PATH": str(tmp_path / "data" / "leyla.sqlite3"),
        "LOG_LEVEL": "WARNING",
        "ENVIRONMENT": "test",
        "IMAGE_GENERATION_ENABLED": "false",
    }
    loaded = Settings.load(env)
    loaded.ensure_dirs()
    return loaded


@pytest_asyncio.fixture
async def db(settings: Settings):
    database = Database(settings.db_path)
    database.connect()
    yield database
    database.close()


class ScriptedProvider(ChatProvider):
    """Returns queued responses, recording the requests it received."""

    name = "scripted"
    supports_json_mode = True

    def __init__(self, *texts: str, fail_with: Exception | None = None) -> None:
        self.texts = list(texts)
        self.requests: list[ChatRequest] = []
        self.fail_with = fail_with

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if self.fail_with is not None:
            raise self.fail_with
        text = self.texts.pop(0) if self.texts else '{"reply": "ok", "remember": []}'
        return ChatResponse(text=text, provider=self.name, model="scripted", usage=TokenUsage(10, 5))


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        payload: Any = None,
        body: str = "",
        data: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._payload = payload if payload is not None else {}
        self._body = body or "{}"
        self._data = data
        self.headers = headers or {}

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return self._body

    async def read(self) -> bytes:
        return self._data


@pytest.fixture
def fake_settings_factory(tmp_path: Path):
    def make(**overrides: str) -> Settings:
        env = {
            "DISCORD_TOKEN": "test-token-not-real",
            "OWNER_DISCORD_ID": "42",
            "AI_PROVIDER": "echo",
            "AI_MODEL": "echo-local",
            "VOICE_PROVIDER": "none",
            "VOICE_MODE": "off",
            "DATA_DIR": str(tmp_path / "data2"),
            "DB_PATH": str(tmp_path / "data2" / "leyla.sqlite3"),
            "LOG_LEVEL": "WARNING",
            "ENVIRONMENT": "test",
            **overrides,
        }
        loaded = Settings.load(env)
        loaded.ensure_dirs()
        return loaded

    return make
