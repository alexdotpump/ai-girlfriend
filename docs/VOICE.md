# Voice notes

Leyla sends **real Discord voice messages** — Ogg/Opus files with a waveform that
play inline in the client, not links or fake attachments.

## Why ElevenLabs v3/v4

Ordinary TTS cannot produce believable breathing, giggles or "laughing while
finishing the sentence". That is a performance, not a waveform effect.

ElevenLabs v3/v4 accept **audio tags** in the text and render them as nonverbal
vocalisations inside a single take, with the same voice identity:

```
[laughs] you actually fell for that?
[sighs] fine. I'll explain it again.
[exhales] okay. that's the whole story.
```

Verified supported cues: `[laughs]`, `[laughs harder]`, `[starts laughing]`,
`[sighs]`, `[exhales]`, `[whispers]`, `[clears throat]`, `[snorts]`,
`[nervous laugh]`, `[excited]`, `[curious]`, `[sarcastic]`, `[mischievously]`.

Why not the alternatives (checked in November 2026):

| Option | Why it is not the default |
| --- | --- |
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

Mood → expression examples:

| Mood | Cues |
| --- | --- |
| playful / excited | `[laughs]`, `[starts laughing]`, `[mischievously]`, `[excited]` |
| happy | `[laughs]` |
| relaxed / sleepy | `[exhales]`, `[sighs]`, `[whispers]` |
| shy | `[nervous laugh]` |
| sarcastic | `[sarcastic]` |
| serious | none — no bits during a serious conversation |
| annoyed / disappointed | `[sighs]` |

## Audio pipeline

```
provider audio (mp3)
   → ffmpeg: loudnorm → libopus 64 kbps → 48 kHz stereo → Ogg
   → duration from the last Ogg granule position − Opus pre-skip (no ffprobe)
   → waveform: decode 8 kHz mono PCM → 128 RMS bars → base64
   → Discord REST: request upload URL → PUT audio/ogg → POST message
     flags=8192 (IS_VOICE_MESSAGE), duration_secs, waveform, no text content
```

Fallbacks:

- ffmpeg missing → the MP3 is sent as a normal playable attachment and the reason
  is logged. No broken voice note is ever sent.
- TTS failure → text reply still delivered.
- Upload failure → logged; no message pretending a voice note exists.

## Budgets

`TTS_DAILY_CHAR_LIMIT=2200`, `TTS_MONTHLY_CHAR_LIMIT=8500` by default — chosen to
stay inside ElevenLabs' free 10k characters/month. Notes are cached by text hash
so replays and tests do not spend credits. `/voice status` shows usage.

At ~300 characters per note, the free plan supports roughly **one short note a
day**, or five a week with longer notes. Raise the limits only with a paid plan.

## How to audition it

```
/voice test
/voice test okay, this is the laughing one. [laughs] see, that was supposed to sound natural.
```

Listen for: is the laughter placed before a thought (natural) rather than mid-word;
does the breath land between sentences; does the voice identity match every note.
If laughter fires too often, lower `VOICE_FREQUENCY`, raise
`VOICE_COOLDOWN_MINUTES`, or set `VOICE_NONVERBAL_ENABLED=false`.

Tuning knobs: `ELEVENLABS_STABILITY` (lower = more expressive, default 0.45),
`ELEVENLABS_STYLE` (0.35), `ELEVENLABS_SIMILARITY` (0.8),
`ELEVENLABS_SPEAKER_BOOST=true`, and `ELEVENLABS_MODEL` (`eleven_v4` default,
falling back to `eleven_v3` then `eleven_multilingual_v2` if your plan rejects it).

## Tested vs. not tested

Tested in this build:

- real speech synthesis through the free `edge-tts` provider (42 KB MP3)
- ffmpeg conversion to a **7.2 s Ogg/Opus** file with `OpusHead`, correct duration
  and a 128-bar waveform
- the exact three-step Discord upload payload (against a fake HTTP layer)
- all gating/cleaning/tag logic, cooldowns, budgets and fallbacks

Not tested, because it needs credentials/accounts this build did not have:

- ElevenLabs tag rendering and subjective naturalness
- an actual Discord upload and playback on your account

After deploying, run `/voice test` and listen. That is the only honest way to
confirm the voice is good enough for you.

## Adding another provider

Implement `leyla.voice.base.VoiceProvider`:

```python
class MyVoice(VoiceProvider):
    name = "myvoice"
    supports_audio_tags = False
    async def synthesize(self, text: str, *, delivery: str = "") -> SpeechAudio: ...
```

Register it in `voice/providers/registry.py` and select it with
`VOICE_PROVIDER=myvoice`. The director, budgets, encoder and sender work unchanged.
