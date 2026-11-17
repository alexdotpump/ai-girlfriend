# Voice notes

Leyla sends **real Discord voice messages** — Ogg/Opus files with a waveform that
play inline in the client, not links or fake attachments.

## Why Groq Orpheus (`canopylabs/orpheus-v1-english`)

Ordinary TTS cannot produce believable breathing or real laughter. That is a
performance, not a waveform effect.

Orpheus renders Canopy Labs' nonverbal **tokens** as actual vocalisations inside
the same take, with the same voice identity — and it never reads the token out
loud. Verified against the live API: the transcript of
`<laugh> Wait, you actually did that?` is just the sentence, while the audio is
1.5–2 s longer than the same line without the token. `<sigh>` behaves the same way.

```
<laugh> you actually fell for that?
<sigh> fine. I'll explain it again.
<chuckle> you are impossible, you know that?
```

Cues used: `<laugh>`, `<chuckle>`, `<sigh>`. Voice: `autumn` (female), with
`diana`/`hannah` as alternates. Leyla never says the word "giggle" — in text or
audio; a laugh is a laugh.

Why not the alternatives (checked in November 2026):

| Option | Why it is not the default |
| --- | --- |
| ElevenLabs v3/v4 | Also expressive (`[laughs]`, `[sighs]`), still supported as `VOICE_PROVIDER=elevenlabs`, but a 10k-character free plan is smaller than Orpheus' pricing and needs another key |
| OpenAI TTS | Natural voice, but only natural-language delivery instructions — no laughter or breaths |
| edge-tts | Free and decent, but SSML-ish prosody only; no nonverbal sounds |
| Google/Azure SSML | Supports pauses and style, not realistic laughter/breathing |
| Local neural TTS (Piper, XTTS) | CPU/RAM heavy on a 308 MB free host; XTTS needs a GPU for speed |
| Stitched stock laughter clips | Exactly what makes hybrid voices sound artificially assembled |

## Single-take principle

Every note is **one synthesis request**. Leyla does not concatenate a laugh clip
onto a TTS fragment, because sentence energy, room tone and prosody never match at
the seam. Expression comes from tags and punctuation inside that one take.

If a provider cannot do tags, the director strips them and the note is plain
speech — it never reads "[laughs]" aloud.

## Direction, not randomness

`voice/director.py` decides:

1. **Should this be spoken?** Modes `off` / `request` / `occasional` / `always`,
   mood voice affinity, energy, a cooldown (default 12 minutes) and budgets.
2. **What text?** Markdown, links, emojis and code are removed; `lol` → "ha ha",
   `omg` → "oh my god", `idk` → "I don't know"; long replies are trimmed on a
   sentence boundary; replies over ~1.8× the limit are not spoken at all.
3. **Which cues?** Mood-specific whitelist, at most **one lead cue + one breath
   cue**, each with its own cooldown (laughter 25–60 minutes, sighs 40 minutes,
   breaths 15 minutes). Expression is deliberately occasional.

Mood → expression examples (canonical names; Orpheus renders them as
`<laugh>`, `<chuckle>`, `<sigh>`):

| Mood | Cues |
| --- | --- |
| playful / excited | `[laughs]`, `[starts laughing]`, `[mischievously]`, `[excited]` |
| happy | `[laughs]` |
| relaxed / sleepy | `[exhales]`, `[sighs]`, `[whispers]` |
| shy | `[nervous laugh]` → `<chuckle>` |
| serious | none — no bits during a serious conversation |
| annoyed / disappointed | `[sighs]` |

## Audio pipeline

```
provider audio (wav from Orpheus, mp3 from the others)
   → ffmpeg: loudnorm → libopus 64 kbps → 48 kHz stereo → Ogg
   → duration from the last Ogg granule position − Opus pre-skip (no ffprobe)
   → waveform: decode 8 kHz mono PCM → 128 RMS bars → base64
   → Discord REST: request upload URL → PUT audio/ogg → POST message
     flags=8192 (IS_VOICE_MESSAGE), duration_secs, waveform, no text content
```

Fallbacks:

- ffmpeg missing → the WAV/MP3 is sent as a normal playable attachment and the
  reason is logged. No broken voice note is ever sent.
- TTS failure → text reply still delivered.
- Upload failure → logged; no message pretending a voice note exists.

## Budgets

`TTS_DAILY_CHAR_LIMIT=2000`, `TTS_MONTHLY_CHAR_LIMIT=8000` by default. Orpheus is
priced per character ($22 / 1M characters ≈ $0.004 for a 190-character note), so
those budgets cap spend at a few cents a month while still allowing several notes
a week. Notes are cached by text hash, so replays and tests cost nothing.
`/voice status` shows usage.

Because the API caps input at 200 characters, `VOICE_MAX_CHARS=190` and every note
is trimmed on a sentence boundary before the call.

## How to audition it

```
/voice test
/voice test okay this is the laughing one, wait — [that one is automatic, not typed]
```

Just use `/voice test` and talk to her normally; the cues are chosen by mood, not
by you. Listen for: does the laugh land before the thought (natural) rather than
mid-word; does the sigh sit on a sentence break; does the voice sound like the same
person every time.

If laughter fires too often, lower `VOICE_FREQUENCY`, raise
`VOICE_COOLDOWN_MINUTES`, or set `VOICE_NONVERBAL_ENABLED=false`. If you want a
different voice, set `GROQ_TTS_VOICE` to `autumn`, `diana` or `hannah`.

## Tested vs. not tested

Tested live on 17 November 2026:

- Groq Orpheus synthesis with `<laugh>`/`<sigh>`: the token is performed, not read
- a real Discord voice message: 9.4 s, 62.5 KB, `audio/ogg`, waveform, `flags: 8192`
- the occasional-voice policy firing on its own inside a real chat turn
- the exact three-step upload against the live Discord API
- ffmpeg conversion, duration from the Ogg granule position, 128-bar waveform
- all gating/cleaning/cue logic, cooldowns, budgets and fallbacks

Not tested: whether *you* like the voice. Play the note in Discord and decide —
that is the only honest test of naturalness.

## Adding another provider

Implement `leyla.voice.base.VoiceProvider`:

```python
class MyVoice(VoiceProvider):
    name = "myvoice"
    supports_audio_tags = False
    tag_style = "elevenlabs"   # or "orpheus" for <laugh>/<sigh> tokens
    max_input_chars = 0        # e.g. 200 for Orpheus-style APIs
    async def synthesize(self, text: str, *, delivery: str = "") -> SpeechAudio: ...
```

Register it in `voice/providers/registry.py` and select it with
`VOICE_PROVIDER=myvoice`. The director, budgets, encoder and sender work unchanged.
