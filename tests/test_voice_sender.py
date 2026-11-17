"""Discord voice-message upload protocol (replayed against a fake HTTP layer)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from leyla.errors import VoiceError
from leyla.voice.audio import PreparedAudio
from leyla.voice.sender import VOICE_MESSAGE_FLAG, send_audio_attachment, send_voice_message


class RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/attachments"):
            return _Response(200, {"attachments": [{"upload_url": "https://cdn.test/put", "upload_filename": "abc.ogg"}]})
        return _Response(200, {"id": "999"})

    def put(self, url: str, **kwargs):
        self.calls.append(("PUT", url, kwargs))
        return _Response(204, {})


class _Response:
    def __init__(self, status: int, payload: dict, body: str = '{"id":"999"}') -> None:
        self.status = status
        self._payload = payload
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._body


def prepared() -> PreparedAudio:
    return PreparedAudio(
        ogg=b"OggS" + b"\x00" * 8000,
        duration=3.25,
        waveform="AAAA",
        transcoded=True,
    )


@pytest.mark.asyncio
async def test_voice_message_upload_protocol(monkeypatch):
    session = RecordingSession()

    async def fake_get_session():
        return session

    import leyla.ai.http as http_module

    monkeypatch.setattr(http_module, "get_session", fake_get_session)

    channel = SimpleNamespace(id=1234)
    bot = SimpleNamespace(http=SimpleNamespace(token="fake-token-value"))
    await send_voice_message(channel, bot, prepared())

    assert [call[0] for call in session.calls] == ["POST", "PUT", "POST"]

    _, attach_url, attach_kwargs = session.calls[0]
    assert attach_url.endswith("/channels/1234/attachments")
    assert attach_kwargs["json"]["files"][0]["file_size"] == len(prepared().ogg)
    assert attach_kwargs["json"]["files"][0]["filename"] == "voice-message.ogg"
    assert attach_kwargs["headers"]["Authorization"] == "Bot fake-token-value"

    _, put_url, put_kwargs = session.calls[1]
    assert put_url == "https://cdn.test/put"
    assert put_kwargs["headers"]["Content-Type"] == "audio/ogg"
    assert put_kwargs["data"] == prepared().ogg

    _, message_url, message_kwargs = session.calls[2]
    assert message_url.endswith("/channels/1234/messages")
    payload = message_kwargs["json"]
    assert payload["flags"] == VOICE_MESSAGE_FLAG == 8192
    attachment = payload["attachments"][0]
    assert attachment["uploaded_filename"] == "abc.ogg"
    assert attachment["duration_secs"] == 3.25
    assert attachment["waveform"] == "AAAA"
    assert "content" not in payload  # Discord rejects text alongside voice notes


@pytest.mark.asyncio
async def test_missing_token_is_reported(monkeypatch):
    channel = SimpleNamespace(id=1)
    bot = SimpleNamespace(http=SimpleNamespace(token=None))
    with pytest.raises(VoiceError):
        await send_voice_message(channel, bot, prepared())


@pytest.mark.asyncio
async def test_upload_failure_is_reported(monkeypatch):
    class FailingSession(RecordingSession):
        def post(self, url, **kwargs):
            if url.endswith("/attachments"):
                return _Response(403, {}, body="forbidden")
            return _Response(200, {})

    async def fake_get_session():
        return FailingSession()

    import leyla.ai.http as http_module

    monkeypatch.setattr(http_module, "get_session", fake_get_session)
    with pytest.raises(VoiceError):
        await send_voice_message(
            SimpleNamespace(id=1), SimpleNamespace(http=SimpleNamespace(token="t")), prepared()
        )


@pytest.mark.asyncio
async def test_attachment_fallback_sends_file():
    sent: dict = {}

    class FakeChannel:
        async def send(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(id=1)

    await send_audio_attachment(FakeChannel(), b"audio-bytes", filename="leyla.mp3")
    assert sent["file"].filename == "leyla.mp3"
    assert sent["allowed_mentions"].everyone is False
