"""Discord delivery of voice notes.

discord.py does not expose Discord's voice-message fields, so we use the
documented REST flow directly:

1. ``POST /channels/{id}/attachments`` → pre-signed upload URL
2. ``PUT`` the Ogg/Opus bytes to that URL
3. ``POST /channels/{id}/messages`` with ``flags=8192`` (IS_VOICE_MESSAGE) and
   ``duration_secs`` + ``waveform`` metadata

The bot token is read from the client's HTTP session at call time and is never
logged, stored or echoed.
"""

from __future__ import annotations

import asyncio
import io

import discord

from ..errors import VoiceError
from ..logging_setup import get_logger
from .audio import PreparedAudio

log = get_logger("leyla.voice.sender")

API_BASE = "https://discord.com/api/v10"
VOICE_MESSAGE_FLAG = 1 << 13


def _token(bot: discord.Client) -> str:
    token = getattr(getattr(bot, "http", None), "token", None)
    if not token:
        raise VoiceError("bot token unavailable for the REST upload")
    return str(token)


async def send_voice_message(
    channel: discord.abc.Messageable,
    bot: discord.Client,
    prepared: PreparedAudio,
    *,
    filename: str = "voice-message.ogg",
    timeout: float = 45.0,
) -> dict:
    """Upload and send a playable Discord voice message."""
    from ..ai.http import get_session

    token = _token(bot)
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        raise VoiceError("voice notes need a real channel, not a user")

    headers = {"Authorization": f"Bot {token}"}
    session = await get_session()

    try:
        async with session.post(
            f"{API_BASE}/channels/{channel_id}/attachments",
            headers={**headers, "Content-Type": "application/json"},
            json={"files": [{"filename": filename, "file_size": len(prepared.ogg), "id": "0"}]},
            timeout=timeout,
        ) as response:
            if response.status != 200:
                detail = (await response.text())[:200]
                raise VoiceError(f"attachment upload request failed ({response.status}): {detail}")
            upload_info = await response.json()

        upload = (upload_info.get("attachments") or [{}])[0]
        upload_url = upload.get("upload_url")
        uploaded_filename = upload.get("upload_filename")
        if not upload_url or not uploaded_filename:
            raise VoiceError("Discord did not return an upload URL")

        async with session.put(
            upload_url,
            data=prepared.ogg,
            headers={"Content-Type": "audio/ogg"},
            timeout=timeout,
        ) as response:
            if response.status not in (200, 201, 204):
                raise VoiceError(f"audio upload failed ({response.status})")

        attachment: dict = {
            "id": "0",
            "filename": filename,
            "uploaded_filename": uploaded_filename,
            "duration_secs": round(max(0.1, prepared.duration), 2),
        }
        if prepared.waveform:
            attachment["waveform"] = prepared.waveform

        async with session.post(
            f"{API_BASE}/channels/{channel_id}/messages",
            headers={**headers, "Content-Type": "application/json"},
            json={"flags": VOICE_MESSAGE_FLAG, "attachments": [attachment]},
            timeout=timeout,
        ) as response:
            body = await response.text()
            if response.status not in (200, 201):
                raise VoiceError(f"voice message rejected ({response.status}): {body[:200]}")
            log.info(
                "voice note sent (%.1fs, %.1f KB, waveform=%s)",
                prepared.duration,
                prepared.size_kb,
                bool(prepared.waveform),
            )
            return await response.json() if body else {}
    except asyncio.TimeoutError as exc:
        raise VoiceError("Discord timed out while uploading the voice note") from exc
    except discord.HTTPException as exc:  # pragma: no cover - defensive
        raise VoiceError(f"Discord HTTP error: {exc}") from exc


async def send_audio_attachment(
    channel: discord.abc.Messageable,
    data: bytes,
    *,
    filename: str = "leyla-voice.mp3",
    content: str | None = None,
) -> None:
    """Fallback: send the audio as a normal attachment (still playable inline)."""
    buffer = io.BytesIO(data)
    buffer.seek(0)
    await channel.send(
        content=content,
        file=discord.File(buffer, filename=filename),
        allowed_mentions=discord.AllowedMentions.none(),
    )
