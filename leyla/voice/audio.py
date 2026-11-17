"""Audio preparation: anything → Discord-ready Ogg/Opus.

Discord voice notes must be Ogg with the Opus codec. We shell out to ffmpeg
(the only reliable encoder on small hosts), and we read the duration straight
from the Ogg granule position so ``ffprobe`` is not required.
"""

from __future__ import annotations

import asyncio
import base64
import math
import shutil
import struct
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..errors import VoiceError
from ..logging_setup import get_logger

log = get_logger("leyla.voice.audio")

CANDIDATE_PATHS = (
    "/usr/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
)


@lru_cache(maxsize=1)
def _discover_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in CANDIDATE_PATHS:
        if Path(candidate).is_file():
            return candidate
    # Dev convenience: imageio-ffmpeg ships a static build. Never installed on
    # the host by default (see requirements-dev.txt).
    try:  # pragma: no cover - depends on optional package
        import imageio_ffmpeg  # type: ignore[import-not-found]

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:  # noqa: BLE001 - optional dependency
        pass
    return None


def resolve_ffmpeg(explicit: str = "") -> str | None:
    """Locate an ffmpeg binary (explicit path → PATH → well-known → imageio)."""
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path)
        log.warning("FFMPEG_PATH=%s does not exist; auto-detecting", explicit)
    return _discover_ffmpeg()


def looks_like_ogg(data: bytes) -> bool:
    return len(data) > 64 and data[:4] == b"OggS"


def og_duration_seconds(data: bytes) -> float:
    """Duration of an Ogg/Opus stream from the last granule position (RFC 7845)."""
    pos = 0
    last_granule = 0
    pre_skip = 0
    while pos + 27 <= len(data):
        if data[pos : pos + 4] != b"OggS":
            break
        header_type = data[pos + 5]
        granule = struct.unpack_from("<q", data, pos + 6)[0]
        segments = data[pos + 26]
        table = data[pos + 27 : pos + 27 + segments]
        body_len = sum(table)
        body_start = pos + 27 + segments
        body_end = body_start + body_len
        if granule > 0:
            last_granule = granule
        if header_type & 0x02:  # beginning of stream page carries OpusHead
            body = data[body_start:body_end]
            if body[:8] == b"OpusHead" and len(body) >= 12:
                pre_skip = struct.unpack_from("<H", body, 10)[0]
        pos = body_end
    if last_granule <= 0:
        return 0.0
    return max(0.0, (last_granule - pre_skip) / 48000.0)


def waveform_base64(pcm: bytes, *, bars: int = 128) -> str | None:
    """Build Discord's base64 waveform from 16-bit mono PCM (values 0-255)."""
    if len(pcm) < bars * 2:
        return None
    samples = struct.unpack_from(f"<{len(pcm) // 2}h", pcm)
    chunk = max(1, len(samples) // bars)
    values = bytearray()
    peak = 1
    for index in range(0, min(len(samples), chunk * bars), chunk):
        window = samples[index : index + chunk]
        rms = math.sqrt(sum(sample * sample for sample in window) / max(1, len(window)))
        level = int(min(255, rms / 120))
        values.append(level)
        peak = max(peak, level)
    if peak < 8:  # near-silent: don't render a flat line
        return None
    return base64.b64encode(bytes(values)).decode("ascii")


def _run_ffmpeg(ffmpeg: str, args: list[str], data: bytes, timeout: float = 90.0) -> bytes:
    try:
        proc = subprocess.run(  # noqa: S603 - controlled arguments
            [ffmpeg, "-hide_banner", "-loglevel", "error", *args],
            input=data,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise VoiceError("ffmpeg is not installed on this host") from exc
    except subprocess.TimeoutExpired as exc:
        raise VoiceError("ffmpeg timed out while processing audio") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise VoiceError(f"ffmpeg failed ({proc.returncode}): {detail}")
    return proc.stdout


def transcode_to_ogg_opus(
    data: bytes, ffmpeg: str, *, bitrate: str = "64k", loudness_normalise: bool = True
) -> bytes:
    """Convert arbitrary audio bytes to Ogg/Opus (48 kHz stereo)."""
    filters = (
        ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
        if loudness_normalise
        else []
    )
    output = _run_ffmpeg(
        ffmpeg,
        [
            "-i", "pipe:0",
            "-vn",
            *filters,
            "-c:a", "libopus",
            "-b:a", bitrate,
            "-ar", "48000",
            "-ac", "2",
            "-application", "voip",
            "-frame_duration", "60",
            "-f", "ogg",
            "pipe:1",
        ],
        data,
    )
    if not looks_like_ogg(output):
        raise VoiceError("ffmpeg did not produce an Ogg stream")
    return output


def decode_pcm(ogg: bytes, ffmpeg: str, *, sample_rate: int = 8000) -> bytes:
    """Decode to mono 16-bit PCM for waveform generation."""
    return _run_ffmpeg(
        ffmpeg,
        ["-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1"],
        ogg,
        timeout=45.0,
    )


@dataclass(slots=True)
class PreparedAudio:
    ogg: bytes
    duration: float
    waveform: str | None
    transcoded: bool
    source_extension: str = "mp3"

    @property
    def size_kb(self) -> float:
        return round(len(self.ogg) / 1024, 1)


async def prepare_voice_note(
    audio: bytes,
    *,
    source_extension: str = "mp3",
    ffmpeg_path: str = "",
    bitrate: str = "64k",
) -> PreparedAudio:
    """Prepare a Discord voice note, or raise :class:`VoiceError`.

    If ffmpeg is unavailable and the audio is already Ogg/Opus, it is sent as-is
    (no waveform). Callers may fall back to a normal audio attachment otherwise.
    """
    if not audio or len(audio) < 256:
        raise VoiceError("synthesised audio was empty")

    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    if looks_like_ogg(audio):
        duration = og_duration_seconds(audio)
        waveform = None
        if ffmpeg:
            try:
                pcm = await asyncio.to_thread(decode_pcm, audio, ffmpeg)
                waveform = waveform_base64(pcm)
            except VoiceError:
                log.debug("waveform generation failed", exc_info=True)
        return PreparedAudio(
            ogg=audio, duration=duration or 0.0, waveform=waveform, transcoded=False
        )

    if not ffmpeg:
        raise VoiceError(
            "ffmpeg is required to convert the generated audio to Ogg/Opus for Discord "
            "voice notes. Install ffmpeg on the host or set FFMPEG_PATH."
        )

    ogg = await asyncio.to_thread(transcode_to_ogg_opus, audio, ffmpeg, bitrate=bitrate)
    duration = og_duration_seconds(ogg)
    waveform = None
    try:
        pcm = await asyncio.to_thread(decode_pcm, ogg, ffmpeg)
        waveform = waveform_base64(pcm)
    except VoiceError:  # pragma: no cover - waveform is cosmetic
        log.debug("waveform generation failed", exc_info=True)

    return PreparedAudio(
        ogg=ogg,
        duration=duration,
        waveform=waveform,
        transcoded=True,
        source_extension=source_extension,
    )
