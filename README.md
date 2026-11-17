# Leyla

A private, expressive Discord AI companion. Leyla is not a support bot: she has a
consistent personality, moods that evolve from the conversation, long-term memory
in SQLite, and real Discord voice notes with nonverbal vocal cues (laughter,
sighs, breaths) generated in a single expressive take.

Owner-only by design: every conversational surface is gated on your Discord user
ID, and nothing responds to anyone else.

---

## What is actually built

| Area | Status | Notes |
| --- | --- | --- |
| Discord bot (discord.py 2.x, slash commands) | Implemented · tested | 10 commands, owner gating, chunked replies, dedupe, graceful shutdown |
| AI provider abstraction + fallback chain | Implemented · tested | Gemini, Groq, OpenAI, OpenRouter, any OpenAI-compatible endpoint, offline `echo` |
| Leyla personality | Implemented · tested | Prompt-level identity, anti-patterns, one-call JSON contract |
| Contextual conversation | Implemented · tested | Bounded history, recent-thread recap, memory injection, mood directives |
| SQLite memory (short + long term) | Implemented · tested | Dedupe, supersede, ranked retrieval, per-item delete, full clear, survives restarts |
| Mood engine (11 moods) | Implemented · tested | Hysteresis, dwell time, decay to baseline, persistence, text-tone inference |
| Voice notes in Discord | Implemented · tested (protocol + audio) | Ogg/Opus encode, duration, waveform, REST voice-message upload |
| Expressive voice | Implemented · **tested live on Discord** | Groq Orpheus `canopylabs/orpheus-v1-english`, voice `autumn`; `<laugh>`, `<chuckle>`, `<sigh>` render as real nonverbal sounds (never read aloud) |
| Voice provider fallbacks | Implemented · one tested live | `edge-tts` (free, no key) produced real speech in this build; ElevenLabs, OpenAI TTS optional |
| Character images | Implemented · tested | 12 mood-matched portraits with one visual identity; optional generation off by default |
| Cost controls | Implemented · tested | Daily/monthly character + request budgets, cooldowns, caching, bounded context |
| KataBump deployment | Documented · verified against published limits | Entry point, `.env`, SQLite, ffmpeg requirement, 4-day renewal |

**Live-verified on 17 November 2026:** gateway login as `Leyla#3471`, a real Gemini
turn, memory extraction, and a real Discord voice message (9.4 s, waveform, flag
8192) posted in the owner's server. One thing is still blocked by the bot's invite:
slash-command sync returns `403 Missing Access` until the bot is re-invited with
the `applications.commands` scope. See [Verification](#verification).

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in DISCORD_TOKEN, OWNER_DISCORD_ID, GEMINI_API_KEY (free), GROQ_API_KEY (voice)

python main.py
```

Voice notes need **ffmpeg** on the host (`ffmpeg -version`). Without it Leyla still
works and falls back to sending the audio as a normal playable attachment.

### Discord application setup

1. <https://discord.com/developers/applications> → **New Application** → **Bot**.
2. **Reset Token** → paste into `DISCORD_TOKEN`.
3. Enable **Message Content Intent** (Bot → Privileged Gateway Intents).
4. Invite with scopes `bot` + `applications.commands` and permissions
   *Send Messages*, *Attach Files*, *Read Message History*, *Use Slash Commands*.
5. Enable Developer Mode in Discord, right-click your name → **Copy User ID** →
   `OWNER_DISCORD_ID`. Set `DISCORD_GUILD_ID` to your private server for instant
   command sync (global sync can take an hour).
6. DM Leyla, or `/setup channel add` in a private channel.

---

## Commands

All commands are owner-only (non-owners get "this one is private.").

| Command | What it does |
| --- | --- |
| `/help` | Command overview |
| `/chat [message] [voice]` | Start a conversation; `voice:true` answers with a voice note |
| `/voice mode off\|request\|occasional\|always` | Voice-note policy |
| `/voice frequency <0-100>` | How often occasional notes happen |
| `/voice status` | Provider, expressions support, remaining character budget |
| `/voice test [text]` | Generate and send a real voice note now |
| `/mood` | Current mood, intensity, energy, time until it fades |
| `/memory list [kind] [page]` | Browse stored memories |
| `/memory add <text> [kind] [importance]` | Teach her something durable |
| `/memory search <query>` | Ranked retrieval preview |
| `/memory reset` | Clear the recent conversation window (memories kept) |
| `/memory-delete <id>` | Forget one memory |
| `/memory-clear <confirm: yes>` | Delete all memories |
| `/setup show` · `/setup channel add\|remove\|clear` · `/setup reset` | Server channel whitelist |
| `/status` | Provider, model, mood, memory count, budgets, uptime, RSS |
| `/photo [scene]` | Send a mood-matched portrait (generation only if enabled) |

Free-form chat: DM her, mention her in a server, or whitelist a channel with
`/setup channel add`. Mentions are stripped before the model sees the message.

---

## Architecture

```
main.py                     KataBump entry point (Startup → PY FILE = main.py)
leyla/
  config.py                 env parsing + validation, no secrets in code
  logging_setup.py          redacting logging (tokens can never be logged)
  security.py               owner checks, sanitisation, rate limiter
  db.py                     SQLite schema, WAL, migrations, usage/cooldowns
  services.py               builds/owns every component once
  bot.py                    gateway client, message routing, error handling
  chat_flow.py              one turn: generate → deliver text → maybe voice
  preferences.py            per-user voice/image preferences
  runtime_config.py         /setup channel whitelist
  ai/
    base.py                 ChatProvider / ChatRequest / ChatResponse
    http.py                 timeouts, retries, backoff, error normalisation
    registry.py             provider factory + fallback chain
    engine.py               prompt assembly, JSON envelope parsing, memory writes
    providers/              gemini.py · openai_compat.py · echo.py
  personality/profile.py    Leyla's identity, style rules, output contract
  mood/                     models.py (11 moods, directives) · engine.py (state)
  memory/                   store.py (long-term) · context.py (short-term) · extractor.py
  voice/
    director.py             decide *whether*, *what*, *which cues*
    providers/              groq_tts.py · elevenlabs.py · openai_tts.py · edge.py · registry.py
    audio.py                ffmpeg → Ogg/Opus, duration, waveform
    sender.py               Discord voice-message REST upload
    service.py              budgets, caching, delivery + fallback
  images/                   assets.py (identity) · providers.py · service.py
  cogs/                     chat.py · commands.py
tests/                      138 tests (see below)
docs/                       deployment · costs · voice · architecture
assets/character/           12 consistent portraits (replaceable)
scripts/make_character_assets.py
```

Design choices worth knowing:

- **One model call per turn.** The reply, mood suggestion, voice hint and memory
  candidates come back in one JSON envelope, so a conversation costs one request.
- **Lexical memory retrieval, not embeddings.** Zero extra API cost, ~50 MB RSS
  total, good enough for hundreds of personal facts.
- **Mood hysteresis.** A new mood must be intense enough, and the current mood has
  dwell time, so she does not flip every message.
- **Voice is additive.** Occasional notes follow the text reply; an explicit
  request replaces it.

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Verification

Run the suite:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

**138 tests pass** (Python 3.12 in this build; all sources are also parsed as
Python 3.10 to match KataBump).

What the tests actually exercise:

- startup: `setup_hook` builds services, registers all 10 commands, shuts down cleanly
- owner-only access: non-owner messages ignored, unauthorized interactions rejected
- missing credentials: config problems reported, `main.py` exits with code 2 and a
  readable message
- providers: Gemini/Groq/OpenAI-compatible request shaping and parsing, retries on
  429/5xx, no retry on 4xx, fallback chain order
- conversation: persona prompt content, JSON envelope parsing, plain-text fallback,
  memory creation, dedupe, supersede, budget enforcement
- memory: relevance ranking, scope isolation, deletion, clearing, cap pruning
- persistence: memories, mood, context and usage survive a full service restart
- mood: baseline, dwell, strong-signal override, blending, decay, inference
- voice direction: gating by mode/frequency/cooldown, speech cleaning, tag choice
  and cooldowns, tag placement, long/link-only rejection
- voice audio: **real** ffmpeg conversion of generated tone/speech to Ogg/Opus,
  OpusHead present, duration from Ogg granule position, 128-bar waveform
- voice delivery: the full three-step Discord upload protocol against a fake HTTP
  layer (flag `8192`, `uploaded_filename`, `duration_secs`, `waveform`, no content)
- images: identity fallback, prompt safety blocklist, daily limit
- hosting: root entry point, minimal requirements, env coverage, no foreign host
  config, 512×512 consistent assets

Also verified manually in this build:

- generated **real speech** with the free `edge-tts` provider → 42 KB MP3 →
  **7.2 s Ogg/Opus**, 50 KB, waveform present (kept locally as
  `artifacts/voice_pipeline_edge.ogg`; build artefacts are git-ignored)
- process footprint with all services initialised: **~50 MB RSS**
- `main.py` refuses to start with missing credentials, no traceback leaked

Live checks run on 17 November 2026 against the real services:

- **Discord gateway login** as `Leyla#3471` (id `1571911547515699210`) in 1 guild;
owner gating verified (`is_owner(you) → True`, stranger → `False`)
- **Gemini turn** through the full engine: in-character reply, mood moved to
  playful, envelope parsed, memory `Alex signed up for a pottery class` stored
- **Groq Orpheus note**: `<laugh> …` → 9.2–9.4 s, 61.7 KB Ogg/Opus, waveform,
  `flags: 8192`, `audio/ogg` — delivered as a real Discord voice message
  (`/voice test` uses the same path)
- **Occasional voice** fired on its own during that turn (`mode=occasional`,
  mood=playful) — so the "now and then" policy works in practice

Not verified: subjective voice quality (I cannot hear the audio — you have to),
and slash-command sync, which is blocked by the bot's invite scope (`403 Missing
Access`). Re-invite with `applications.commands` and restart to fix that.

---

## Cost model

Starter configuration is **€0/month** within free tiers:

- **Gemini free tier** — no card, free Flash models (`gemini-flash-latest`).
- **Groq Orpheus TTS** — $22 per million characters, i.e. about **$0.004 for a
  190-character note**. A handful of notes a day is cents per month; the character
  budget stops it from ever surprising you.
- **KataBump free plan** — 308 MB RAM / 716 MB disk, renewed every 4 days.
- Discord, SQLite, `edge-tts`, Pollinations — free.

Optional paid paths: ElevenLabs Starter ($6/mo, 30k credits, commercial licence),
OpenAI TTS/images, extra hosting. Full table with verified limits:
[docs/COSTS_AND_KEYS.md](docs/COSTS_AND_KEYS.md).

---

## Deploy to KataBump

Step-by-step: [docs/KATABUMP_DEPLOYMENT.md](docs/KATABUMP_DEPLOYMENT.md).

Short version: create a **Python** server → Startup: `PY FILE = main.py`,
Python 3.11 → upload the repo (ZIP or SFTP) so `main.py`, `requirements.txt`,
`leyla/` and `assets/` are at the root → upload `.env` → restart → run
`/voice test`. Renew the free server every 4 days or it is suspended and then
deleted.

---

## Known limitations

- **Single process, local SQLite.** Correct for one owner on one host; not
  multi-instance. Back up `data/leyla.sqlite3` via SFTP.
- **ffmpeg is a host dependency.** Voice notes need it for Ogg/Opus. Without it
  audio is sent as an MP3 attachment instead (playable, but not a voice note).
- **Orpheus accepts 200 characters per request.** Voice notes are therefore short
  (~190 characters by default) and nothing is stitched together to fake a longer
  one.
- **Free Gemini models are busy.** `gemini-flash-latest` sometimes answers 503
  "high demand"; Leyla then tries `AI_MODEL_FALLBACKS` (Gemini 3.5 Flash, then
  Flash-Lite) so the conversation still answers. That failover can take up to a
  minute at peak times.
- **Nonverbal cues are probabilistic.** `<laugh>`/`<sigh>` usually render, not
  always; they are capped at two per note with cooldowns measured in minutes.
- **Free-tier budgets stop features, they do not bill you.** When a budget is hit,
  Leyla says so instead of silently spending.
- **Memory retrieval is lexical.** Very paraphrased queries may miss a fact; use
  `/memory search` to check.
- **Voice notes do not join voice channels.** They are sent as Discord voice
  messages, which needs no voice connection.
- **Free KataBump always-on:** 4-day manual renewal. Miss it and the server is
  suspended for a day, then deleted.
- **Generated portraits are placeholders.** They are abstract, consistent
  placeholders — replace them with your character art (same filenames).

---

## Privacy and security

- Owner ID only; no public companion mode, no allowlist of strangers.
- API keys come from `.env` only; they are never logged (a redaction filter scrubs
  token-shaped strings from every log record).
- Memories and preferences are scoped to your user ID and stored locally.
- Only bounded context is retained (80 rows / 14 days), never an unlimited log.
- Gemini's free tier may use prompts to improve Google products; use a paid key if
  that matters to you.
- Never commit `.env` or the `data/` directory.
