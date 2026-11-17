"""Expressive voice notes: direction, synthesis, audio preparation, delivery."""

from .audio import PreparedAudio, prepare_voice_note, resolve_ffmpeg
from .base import SpeechAudio, VoiceProvider
from .director import VoiceDirector, VoicePlan
from .service import VoiceResult, VoiceService

__all__ = [
    "PreparedAudio",
    "SpeechAudio",
    "VoiceDirector",
    "VoicePlan",
    "VoiceProvider",
    "VoiceResult",
    "VoiceService",
    "prepare_voice_note",
    "resolve_ffmpeg",
]
