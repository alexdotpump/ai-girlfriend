"""Real audio pipeline tests (ffmpeg required).

These generate actual audio bytes and verify the files Discord receives are
genuine Ogg/Opus streams with a sane duration and waveform. They cannot verify
ElevenLabs voice naturalness — that requires an API key (documented in README).
"""

from __future__ import annotations

import base64
import subprocess

import pytest

from leyla.errors import VoiceError
from leyla.voice.audio import (
    looks_like_ogg,
    og_duration_seconds,
    prepare_voice_note,
    resolve_ffmpeg,
    waveform_base64,
)

FFMPEG = resolve_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is required for audio tests")


def _tone(seconds: float = 1.0, fmt: str = "wav") -> bytes:
    codec = ["-c:a", "pcm_s16le"] if fmt == "wav" else ["-c:a", "libmp3lame", "-b:a", "64k"]
    result = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
            *codec, "-f", fmt, "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return result.stdout


@pytest.mark.asyncio
async def test_wav_is_converted_to_playable_ogg_opus():
    prepared = await prepare_voice_note(_tone(1.0))
    assert looks_like_ogg(prepared.ogg)
    assert prepared.ogg[:4] == b"OggS"
    assert prepared.transcoded is True
    assert 0.6 <= prepared.duration <= 1.8
    # Discord requires the Ogg body to carry an OpusHead packet.
    assert b"OpusHead" in prepared.ogg[:200]
    assert prepared.size_kb > 1


@pytest.mark.asyncio
async def test_waveform_is_discord_shaped():
    prepared = await prepare_voice_note(_tone(0.8))
    assert prepared.waveform
    raw = base64.b64decode(prepared.waveform)
    assert 32 <= len(raw) <= 256
    assert max(raw) > 0


@pytest.mark.asyncio
async def test_mp3_input_is_transcoded():
    prepared = await prepare_voice_note(_tone(0.7, fmt="mp3"))
    assert looks_like_ogg(prepared.ogg)
    assert prepared.duration > 0.3


@pytest.mark.asyncio
async def test_ogg_input_is_passed_through():
    prepared = await prepare_voice_note(_tone(0.5))
    prepared_again = await prepare_voice_note(prepared.ogg)
    assert prepared_again.transcoded is False
    assert prepared_again.ogg[:4] == b"OggS"


@pytest.mark.asyncio
async def test_empty_audio_is_rejected():
    with pytest.raises(VoiceError):
        await prepare_voice_note(b"")


@pytest.mark.asyncio
async def test_undecodable_audio_is_rejected():
    with pytest.raises(VoiceError):
        await prepare_voice_note(b"not audio at all" * 100)


def test_duration_parser_handles_garbage():
    assert og_duration_seconds(b"") == 0.0
    assert og_duration_seconds(b"NOPE" * 100) == 0.0


def test_waveform_of_silence_is_none():
    assert waveform_base64(b"\x00\x00" * 500) is None
    assert waveform_base64(b"") is None


def test_resolve_ffmpeg_prefers_explicit_path(tmp_path):
    assert resolve_ffmpeg("/definitely/not/here") == FFMPEG
    assert resolve_ffmpeg() == FFMPEG
