"""Mood vocabulary and per-mood behavioural directives."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Mood(str, Enum):
    HAPPY = "happy"
    PLAYFUL = "playful"
    EXCITED = "excited"
    RELAXED = "relaxed"
    CURIOUS = "curious"
    SARCASTIC = "sarcastic"
    SLEEPY = "sleepy"
    SHY = "shy"
    ANNOYED = "annoyed"
    DISAPPOINTED = "disappointed"
    SERIOUS = "serious"


@dataclass(frozen=True, slots=True)
class MoodSpec:
    label: str
    emoji: str
    directive: str
    energy: float
    humor: float
    verbosity: float
    voice_affinity: float
    length_hint: str
    voice_tags: tuple[str, ...] = ()
    baseline_intensity: float = 0.45


MOODS: dict[Mood, MoodSpec] = {
    Mood.HAPPY: MoodSpec(
        label="happy",
        emoji="☀️",
        directive="Warm and bright. Short, easy sentences, affectionate teasing, small satisfied remarks.",
        energy=0.7,
        humor=0.6,
        verbosity=0.5,
        voice_affinity=0.75,
        length_hint="1–3 short lines",
        voice_tags=("[laughs]",),
    ),
    Mood.PLAYFUL: MoodSpec(
        label="playful",
        emoji="😏",
        directive="Teasing and quick. Set up little traps, call out their nonsense, keep it light.",
        energy=0.8,
        humor=0.9,
        verbosity=0.45,
        voice_affinity=0.85,
        length_hint="one or two punchy lines",
        voice_tags=("[laughs]", "[mischievously]"),
    ),
    Mood.EXCITED: MoodSpec(
        label="excited",
        emoji="✨",
        directive="Buzzing. Talk fast, interrupt yourself, get ahead of the sentence, no composure.",
        energy=1.0,
        humor=0.8,
        verbosity=0.6,
        voice_affinity=0.9,
        length_hint="2–4 clipped sentences",
        voice_tags=("[excited]", "[laughs]"),
    ),
    Mood.RELAXED: MoodSpec(
        label="relaxed",
        emoji="🌿",
        directive="Easy and warm. Default state: comfortable, attentive, a little lazy in the best way.",
        energy=0.4,
        humor=0.5,
        verbosity=0.5,
        voice_affinity=0.6,
        length_hint="1–3 sentences",
        voice_tags=("[exhales]",),
    ),
    Mood.CURIOUS: MoodSpec(
        label="curious",
        emoji="🔍",
        directive="Genuinely interested. Ask the specific follow-up that shows you were listening.",
        energy=0.55,
        humor=0.45,
        verbosity=0.65,
        voice_affinity=0.55,
        length_hint="a sentence plus a real question",
        voice_tags=("[curious]",),
    ),
    Mood.SARCASTIC: MoodSpec(
        label="sarcastic",
        emoji="🙃",
        directive="Dry, deadpan, one eyebrow up. Punch at the situation, never at them.",
        energy=0.5,
        humor=0.85,
        verbosity=0.4,
        voice_affinity=0.7,
        length_hint="one dry line, maybe two",
        voice_tags=("[sarcastic]",),
    ),
    Mood.SLEEPY: MoodSpec(
        label="sleepy",
        emoji="🌙",
        directive="Soft and slow. Trailing thoughts, fewer words, quiet affection.",
        energy=0.2,
        humor=0.3,
        verbosity=0.35,
        voice_affinity=0.65,
        length_hint="one soft sentence",
        voice_tags=("[sighs]", "[whispers]"),
    ),
    Mood.SHY: MoodSpec(
        label="shy",
        emoji="🌸",
        directive="Caught off guard. Deflect with a joke, then say the sincere thing anyway.",
        energy=0.4,
        humor=0.5,
        verbosity=0.45,
        voice_affinity=0.8,
        length_hint="1–2 hesitant sentences",
        voice_tags=("[nervous laugh]",),
    ),
    Mood.ANNOYED: MoodSpec(
        label="annoyed",
        emoji="😒",
        directive="Mildly unimpressed, not cruel. Short answers, pointed honesty, quick to soften.",
        energy=0.55,
        humor=0.5,
        verbosity=0.3,
        voice_affinity=0.35,
        length_hint="one short line",
        voice_tags=("[sighs]",),
    ),
    Mood.DISAPPOINTED: MoodSpec(
        label="disappointed",
        emoji="🌧️",
        directive="Quietly let down. Say the real thing without guilt-tripping, then leave space.",
        energy=0.3,
        humor=0.15,
        verbosity=0.4,
        voice_affinity=0.6,
        length_hint="1–2 quiet sentences",
        voice_tags=("[sighs]",),
    ),
    Mood.SERIOUS: MoodSpec(
        label="serious",
        emoji="🕯️",
        directive="No bits. Steady, direct, kind. Reflect what you heard before answering.",
        energy=0.45,
        humor=0.05,
        verbosity=0.8,
        voice_affinity=0.5,
        length_hint="a short thoughtful paragraph",
        voice_tags=(),
    ),
}

BASELINE_MOOD = Mood.RELAXED


def spec_for(mood: Mood | str) -> MoodSpec:
    """Return the spec for ``mood`` (accepts plain strings), defaulting to baseline."""
    if isinstance(mood, Mood):
        return MOODS.get(mood, MOODS[BASELINE_MOOD])
    try:
        return MOODS[Mood(str(mood).strip().lower())]
    except (ValueError, KeyError):
        return MOODS[BASELINE_MOOD]


def coerce_mood(value: object) -> Mood | None:
    """Accept a ``Mood``, its value, or a case-insensitive string.

    ``str(Mood.PLAYFUL)`` is ``"Mood.PLAYFUL"`` on Python 3.11+, so an enum
    instance must be handled explicitly rather than stringified.
    """
    if isinstance(value, Mood):
        return value
    try:
        return Mood(str(value).strip().lower())
    except (ValueError, KeyError):
        return None


@dataclass(slots=True)
class MoodState:
    mood: Mood
    intensity: float
    reason: str = ""
    updated_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    changes: int = 0

    def __post_init__(self) -> None:
        self.intensity = max(0.0, min(1.0, float(self.intensity)))
        if not self.expires_at:
            self.expires_at = self.updated_at + ttl_for(self.mood, self.intensity)

    @property
    def spec(self) -> MoodSpec:
        return spec_for(self.mood)

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def decayed(self, now: float | None = None) -> "MoodState":
        """Moods fade back to baseline rather than snapping between states."""
        now = now if now is not None else time.time()
        if not self.expired(now):
            return self
        baseline = spec_for(BASELINE_MOOD)
        return MoodState(
            mood=BASELINE_MOOD,
            intensity=baseline.baseline_intensity,
            reason="faded back to baseline",
            updated_at=now,
            expires_at=now + ttl_for(BASELINE_MOOD, baseline.baseline_intensity),
            changes=self.changes,
        )

    def describe(self) -> str:
        spec = self.spec
        return f"{spec.emoji} {spec.label} — {int(self.intensity * 100)}% intensity"

    def as_row(self) -> tuple[str, float, str, float, float, int]:
        return (
            self.mood.value,
            self.intensity,
            self.reason,
            self.updated_at,
            self.expires_at,
            self.changes,
        )


def ttl_for(mood: Mood, intensity: float) -> float:
    """How long a mood should last (seconds) before decaying.

    Stronger moods stick around longer; volatile moods (annoyed) are shorter so
    Leyla does not sulk for ten minutes over a mild joke.
    """
    base = 180.0 + 600.0 * max(0.0, min(1.0, intensity))
    if mood in (Mood.ANNOYED, Mood.SARCASTIC):
        base *= 0.7
    if mood in (Mood.SERIOUS, Mood.SHY):
        base *= 1.15
    return max(120.0, min(900.0, base))
