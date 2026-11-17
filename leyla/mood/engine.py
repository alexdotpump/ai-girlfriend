"""Mood state machine.

Moods change because of what was said, not on a timer. The engine combines the
model's own suggestion with a heuristic reading of the user's message, applies
hysteresis (so a single flat joke cannot flip her mood every thirty seconds),
and persists the result in SQLite.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from ..db import Database
from ..logging_setup import get_logger
from .models import (
    BASELINE_MOOD,
    Mood,
    MoodState,
    coerce_mood,
    spec_for,
    ttl_for,
)

log = get_logger("leyla.mood")

#: Moods cannot change more often than this (seconds) unless the signal is strong.
MIN_DWELL_SECONDS = 20.0
#: A different mood must be at least this intense to take over.
SWITCH_THRESHOLD = 0.3

_KEYWORDS: dict[Mood, tuple[str, ...]] = {
    Mood.PLAYFUL: ("lol", "lmao", "haha", "hehe", "😂", "🤣", "joking", "kidding", "teasing", "😏"),
    Mood.EXCITED: ("omg", "yesss", "finally", "can't wait", "cant wait", "!!", "🎉", "amazing", "let's go"),
    Mood.HAPPY: ("good news", "great", "happy", "proud", "🥰", "😊", "love this"),
    Mood.CURIOUS: ("how", "why", "what if", "wonder", "tell me", "?"),
    Mood.SARCASTIC: ("sure", "obviously", "genius", "wow thanks", "🙄", "brilliant"),
    Mood.SLEEPY: ("tired", "sleepy", "sleep", "3am", "4am", "can't sleep", "insomnia", "exhausted", "😴"),
    Mood.SHY: ("cute", "pretty", "beautiful", "i like you", "miss you", "❤", "🥺", "blush"),
    Mood.ANNOYED: ("shut up", "stop", "annoying", "stupid", "idiot", "wtf", "hate"),
    Mood.DISAPPOINTED: ("forgot", "again", "whatever", "never mind", "nevermind", "left me", "ignored"),
    Mood.SERIOUS: (
        "died", "death", "hospital", "sick", "anxious", "anxiety", "depressed", "panic",
        "scared", "afraid", "help me", "crying", "broke up", "fired", "funeral",
    ),
}

_EXCITED_TOKENS = re.compile(r"[!?]{2,}")
_NEGATION = re.compile(r"\b(not|never|no)\b")

#: Moods that need a serious signal (not just an incidental keyword).
_GUARDED = {Mood.SERIOUS, Mood.ANNOYED, Mood.DISAPPOINTED, Mood.SHY}


@dataclass(slots=True)
class MoodSuggestion:
    mood: Mood
    intensity: float
    reason: str = ""


def infer_mood_from_text(text: str) -> MoodSuggestion | None:
    """Cheap heuristic read of the user's message, used when the model is vague."""
    if not text or len(text.strip()) < 3:
        return None
    lowered = text.lower()
    scores: dict[Mood, float] = {}

    for mood, keywords in _KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in lowered)
        if not hits:
            continue
        score = min(1.0, 0.35 + 0.25 * hits)
        # A question mark alone is a weak curiosity signal.
        if mood is Mood.CURIOUS:
            score = min(score, 0.5)
        if mood in _GUARDED and _NEGATION.search(lowered) and mood is Mood.ANNOYED:
            score *= 0.5
        scores[mood] = score

    if _EXCITED_TOKENS.search(text):
        scores[Mood.EXCITED] = max(scores.get(Mood.EXCITED, 0.0), 0.62)
    if lowered.isupper() and len(lowered) > 6:
        scores[Mood.ANNOYED] = max(scores.get(Mood.ANNOYED, 0.0), 0.4)

    if not scores:
        return None
    mood, score = max(scores.items(), key=lambda item: item[1])
    return MoodSuggestion(mood=mood, intensity=round(score, 2), reason="inferred from message tone")


class MoodEngine:
    """Persistent mood with graceful transitions."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, scope_id: str, *, now: float | None = None) -> MoodState:
        now = now if now is not None else time.time()
        row = await self.db.fetchone("SELECT * FROM mood_state WHERE scope_id = ?", (scope_id,))
        if row is None:
            state = self._baseline()
            await self._persist(scope_id, state)
            return state
        state = MoodState(
            mood=coerce_mood(row["mood"]) or BASELINE_MOOD,
            intensity=float(row["intensity"]),
            reason=row["reason"] or "",
            updated_at=float(row["updated_at"]),
            expires_at=float(row["expires_at"]),
            changes=int(row["changes"] or 0),
        )
        decayed = state.decayed(now)
        if decayed is not state and decayed.mood is not state.mood:
            await self._persist(scope_id, decayed)
            return decayed
        return decayed

    async def apply(
        self,
        scope_id: str,
        suggestion: MoodSuggestion | None,
        *,
        user_text: str | None = None,
        now: float | None = None,
    ) -> MoodState:
        """Blend a suggestion into the current mood and persist the result."""
        now = now if now is not None else time.time()
        current = await self.get(scope_id, now=now)

        candidate = suggestion or infer_mood_from_text(user_text or "")
        if candidate is None:
            return current

        candidate.mood = coerce_mood(candidate.mood) or current.mood
        candidate.intensity = max(0.0, min(1.0, candidate.intensity))

        # Confidence floor: weak signals should not move her at all.
        if candidate.intensity < 0.3 and candidate.mood is not current.mood:
            return current

        elapsed = max(0.0, now - current.updated_at)
        strong = candidate.intensity >= 0.8
        # A fresh (or just-faded) baseline has no invested mood yet, so the first
        # clear signal should land immediately instead of being held by hysteresis.
        baseline_like = current.changes == 0 and current.reason in {
            "baseline",
            "faded back to baseline",
        }

        if candidate.mood is current.mood:
            blended = min(1.0, current.intensity * 0.6 + candidate.intensity * 0.6)
            new_state = MoodState(
                mood=current.mood,
                intensity=blended,
                reason=candidate.reason or current.reason,
                updated_at=now,
                expires_at=now + ttl_for(current.mood, blended),
                changes=current.changes,
            )
        elif elapsed < MIN_DWELL_SECONDS and not strong and not baseline_like:
            # Too soon and not compelling: keep the mood, let it settle.
            new_state = current
        elif (
            candidate.intensity < SWITCH_THRESHOLD
            and candidate.intensity <= current.intensity
            and not baseline_like
        ):
            new_state = current
        else:
            new_state = MoodState(
                mood=candidate.mood,
                intensity=max(candidate.intensity, spec_for(candidate.mood).baseline_intensity),
                reason=candidate.reason,
                updated_at=now,
                expires_at=now + ttl_for(candidate.mood, candidate.intensity),
                changes=current.changes + 1,
            )

        if new_state is not current:
            await self._persist(scope_id, new_state)
        return new_state

    async def reset(self, scope_id: str) -> MoodState:
        state = self._baseline()
        await self._persist(scope_id, state)
        return state

    async def _persist(self, scope_id: str, state: MoodState) -> None:
        await self.db.execute(
            "INSERT INTO mood_state(scope_id, mood, intensity, reason, updated_at, expires_at, changes) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope_id) DO UPDATE SET "
            "mood=excluded.mood, intensity=excluded.intensity, reason=excluded.reason, "
            "updated_at=excluded.updated_at, expires_at=excluded.expires_at, changes=excluded.changes",
            (scope_id, *state.as_row()),
        )

    @staticmethod
    def _baseline() -> MoodState:
        spec = spec_for(BASELINE_MOOD)
        now = time.time()
        return MoodState(
            mood=BASELINE_MOOD,
            intensity=spec.baseline_intensity,
            reason="baseline",
            updated_at=now,
            expires_at=now + ttl_for(BASELINE_MOOD, spec.baseline_intensity),
        )
