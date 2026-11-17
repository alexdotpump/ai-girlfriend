"""Voice direction.

Decides *whether* a reply should be spoken, *what* text is spoken, and *which*
nonverbal cues (if any) belong in it. Kept separate from synthesis so the
decision logic is unit-testable without touching an API.

Deliberate design choices:
* one synthesis request per note — stitching pre-recorded laughs onto TTS
  fragments is exactly what makes hybrid voices sound assembled;
* at most two tags, drawn from mood-appropriate whitelists, with per-tag
  cooldowns so she does not giggle every single note;
* cheap heuristics instead of an extra LLM call.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from ..db import Database
from ..logging_setup import get_logger
from ..mood.models import Mood, MoodState, spec_for

log = get_logger("leyla.voice.director")

#: Tags verified in ElevenLabs v3/v4 documentation.
SUPPORTED_TAGS = {
    "[laughs]",
    "[laughs harder]",
    "[starts laughing]",
    "[sighs]",
    "[exhales]",
    "[whispers]",
    "[clears throat]",
    "[curious]",
    "[mischievously]",
    "[excited]",
    "[sarcastic]",
    "[snorts]",
    "[nervous laugh]",
}

#: How long each nonverbal cue is rested after use (seconds).
TAG_COOLDOWNS = {
    "[laughs]": 1500,
    "[laughs harder]": 3600,
    "[starts laughing]": 3600,
    "[snorts]": 5400,
    "[nervous laugh]": 5400,
    "[sighs]": 2400,
    "[exhales]": 900,
    "[clears throat]": 5400,
    "[whispers]": 2400,
    "[curious]": 600,
    "[sarcastic]": 900,
    "[excited]": 600,
    "[mischievously]": 900,
}

_SPEECH_FIXES = (
    (re.compile(r"\b(lol|lmao|lmfao|rofl)\b", re.IGNORECASE), "ha ha"),
    (re.compile(r"\b(omg)\b", re.IGNORECASE), "oh my god"),
    (re.compile(r"\b(idk)\b", re.IGNORECASE), "I don't know"),
    (re.compile(r"\b(tbh)\b", re.IGNORECASE), "to be honest"),
    (re.compile(r"\b(ngl)\b", re.IGNORECASE), "not gonna lie"),
    (re.compile(r"\b(brb)\b", re.IGNORECASE), "be right back"),
    (re.compile(r"\b(rn)\b", re.IGNORECASE), "right now"),
    (re.compile(r"\b(u|ya)\b", re.IGNORECASE), "you"),
    (re.compile(r"\b(ur)\b", re.IGNORECASE), "your"),
    (re.compile(r"\b(im)\b", re.IGNORECASE), "I'm"),
    (re.compile(r"\b(dont)\b", re.IGNORECASE), "don't"),
    (re.compile(r"\b(cant)\b", re.IGNORECASE), "can't"),
    (re.compile(r"\b(its)\b", re.IGNORECASE), "it's"),
    (re.compile(r"\b(gonna)\b", re.IGNORECASE), "going to"),
    (re.compile(r"\b(wanna)\b", re.IGNORECASE), "want to"),
    (re.compile(r"\b(ok|okay)\b", re.IGNORECASE), "okay"),
)

_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_MD_MARKS = re.compile(r"(\*\*|__|~~|`{1,3}|^#{1,6}\s|^>\s?)", re.MULTILINE)
_URL = re.compile(r"https?://\S+|www\.\S+")
_EMOJI = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002700-\U000027BF" "\U0001F1E6-\U0001F1FF"
    "\U00002600-\U000026FF" "\U0000FE0F" "\U00002B00-\U00002BFF" "]+",
    flags=re.UNICODE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_TAG_ANY = re.compile(r"\[[^\]]{1,40}\]")

#: Cues that belong in front of the line (a reaction before speaking).
_LEAD_TAGS = {
    "[laughs]",
    "[starts laughing]",
    "[sighs]",
    "[excited]",
    "[curious]",
    "[sarcastic]",
    "[mischievously]",
    "[nervous laugh]",
    "[whispers]",
}
#: Cues that belong mid-sentence (breath / throat / harder laughter).
_MID_TAGS = {"[exhales]", "[clears throat]", "[laughs harder]", "[snorts]"}


@dataclass(slots=True)
class VoicePlan:
    should_send: bool
    text: str = ""
    spoken_text: str = ""
    reason: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def characters(self) -> int:
        return len(self.text)


class VoiceDirector:
    def __init__(
        self,
        db: Database,
        *,
        max_chars: int = 700,
        cooldown_minutes: int = 12,
        nonverbal_enabled: bool = True,
        rng: random.Random | None = None,
    ) -> None:
        self.db = db
        self.max_chars = max_chars
        self.cooldown_minutes = cooldown_minutes
        self.nonverbal_enabled = nonverbal_enabled
        self.rng = rng or random.Random()

    async def plan(
        self,
        *,
        scope_id: str,
        reply: str,
        mood: MoodState,
        request: str = "auto",
        mode: str = "occasional",
        frequency: float = 0.2,
        energy: str = "medium",
        supports_tags: bool = True,
    ) -> VoicePlan:
        """Decide whether (and how) this reply becomes a voice note.

        ``request`` is ``explicit`` for /voice test or when the owner asks for a
        voice reply, ``auto`` for normal conversation.
        """
        if mode == "off":
            return VoicePlan(False, reason="voice mode is off")

        spoken = clean_for_speech(reply, max_chars=self.max_chars)
        if not spoken:
            return VoicePlan(False, reason="nothing speakable in this reply (links/code only)")
        if len(spoken) < 12:
            return VoicePlan(False, reason="reply too short to be worth speaking")
        if len(reply) > self.max_chars * 1.8:
            return VoicePlan(False, reason="reply too long for a voice note")

        explicit = request == "explicit"
        if not explicit:
            if mode == "request":
                return VoicePlan(False, reason="voice mode is request-only")
            if mode == "occasional":
                if await self.db.get_cooldown(f"voice:{scope_id}") > 0:
                    return VoicePlan(False, reason="voice cooldown active")
                probability = self._probability(mood, frequency, energy)
                if self.rng.random() > probability:
                    return VoicePlan(False, reason=f"not selected this turn (p={probability:.2f})")

        tags: tuple[str, ...] = ()
        if supports_tags and self.nonverbal_enabled:
            tags = await self._choose_tags(scope_id, mood, spoken, energy)

        text = compose_spoken_text(spoken, tags)
        return VoicePlan(
            should_send=True,
            text=text,
            spoken_text=spoken,
            reason=("explicitly requested" if explicit else f"mode={mode}, mood={mood.mood.value}"),
            tags=tags,
        )

    def _probability(self, mood: MoodState, frequency: float, energy: str) -> float:
        spec = spec_for(mood.mood)
        multiplier = 0.4 + 0.9 * spec.voice_affinity
        multiplier *= {"low": 0.7, "medium": 1.0, "high": 1.25}.get(energy, 1.0)
        if mood.mood in {Mood.SERIOUS, Mood.DISAPPOINTED}:
            multiplier *= 0.8
        return max(0.0, min(0.9, frequency * multiplier))

    async def _choose_tags(
        self, scope_id: str, mood: MoodState, spoken: str, energy: str
    ) -> tuple[str, ...]:
        """Pick at most two mood-appropriate nonverbal cues that are off cooldown."""
        spec = spec_for(mood.mood)
        candidates = [tag for tag in spec.voice_tags if tag in SUPPORTED_TAGS]
        if energy == "high" and mood.mood in {Mood.PLAYFUL, Mood.EXCITED, Mood.HAPPY}:
            candidates.append("[starts laughing]")
        if energy == "low" and mood.mood in {Mood.SLEEPY, Mood.RELAXED, Mood.DISAPPOINTED}:
            candidates.append("[exhales]")
        if not candidates:
            return ()

        # Long notes get a breath cue even in a neutral mood.
        if len(spoken) > 320 and "[exhales]" not in candidates:
            candidates.append("[exhales]")

        chosen: list[str] = []
        for tag in dict.fromkeys(candidates):
            cooldown_key = f"tag:{scope_id}:{tag}"
            if await self.db.get_cooldown(cooldown_key) > 0:
                continue
            chosen.append(tag)
            if len(chosen) >= 2:
                break
        if not chosen:
            return ()
        # Laughter is only convincing if it is occasional, not on every note.
        if any("laugh" in tag for tag in chosen) and self.rng.random() < 0.25:
            chosen = [tag for tag in chosen if "laugh" not in tag]
        if not chosen:
            return ()
        # Keep one leading reaction plus at most one mid-sentence breath: two
        # reactions stacked at the start sounds like a soundboard, not a person.
        lead = next((tag for tag in chosen if tag in _LEAD_TAGS), None)
        mid = next((tag for tag in chosen if tag in _MID_TAGS), None)
        return tuple(tag for tag in (lead, mid) if tag)

    async def mark_sent(self, scope_id: str, plan: VoicePlan) -> None:
        """Apply cooldowns once a note has actually been delivered."""
        if self.cooldown_minutes > 0:
            await self.db.set_cooldown(f"voice:{scope_id}", self.cooldown_minutes * 60)
        for tag in plan.tags:
            await self.db.set_cooldown(f"tag:{scope_id}:{tag}", TAG_COOLDOWNS.get(tag, 1800))


def clean_for_speech(text: str, *, max_chars: int = 700) -> str:
    """Turn a chat reply into something a human would actually say aloud."""
    if not text or "```" in text:
        return ""
    cleaned = _MD_LINK.sub(r"\1", text)
    cleaned = _MD_MARKS.sub("", cleaned)
    cleaned = _URL.sub("", cleaned)
    cleaned = _EMOJI.sub("", cleaned)
    cleaned = _TAG_ANY.sub("", cleaned)  # never speak a tag originally present
    cleaned = re.sub(r"[*_~|]", "", cleaned)
    cleaned = re.sub(r"\s*\n\s*", ". ", cleaned)
    cleaned = re.sub(r"\.{2,}", "…", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)

    for pattern, replacement in _SPEECH_FIXES:
        cleaned = pattern.sub(replacement, cleaned)

    cleaned = cleaned.strip(" -–—.,;:")
    if not cleaned:
        return ""

    # Prefer ending on a sentence boundary when trimming.
    if len(cleaned) > max_chars:
        window = cleaned[:max_chars]
        cut = max(window.rfind("."), window.rfind("!"), window.rfind("?"), window.rfind("…"))
        cleaned = window[: cut + 1] if cut > max_chars * 0.5 else window.rsplit(" ", 1)[0]

    if cleaned and cleaned[-1] not in ".!?…":
        cleaned += "."
    return cleaned


def compose_spoken_text(spoken: str, tags: tuple[str, ...]) -> str:
    """Place cues where they sound natural: reactions lead, breaths mid-sentence."""
    if not tags:
        return spoken

    opening = [tag for tag in tags if tag in _LEAD_TAGS]
    mid = [tag for tag in tags if tag in _MID_TAGS]

    text = spoken
    if opening:
        text = f"{opening[0]} {text}"

    if mid:
        sentences = _SENTENCE_END.split(text)
        if len(sentences) > 1:
            insert_at = min(1, len(sentences) - 1)
            sentences[insert_at] = f"{mid[0]} {sentences[insert_at]}"
            text = " ".join(sentences)
        else:
            text = f"{text} {mid[0]}"
    return re.sub(r"\s{2,}", " ", text).strip()
