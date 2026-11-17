# Architecture

## One turn, end to end

```
Discord message / slash command
        │
        ▼
LeylaBot.on_message                     (owner check, channel policy, dedupe)
        │
        ▼
chat_flow.run_turn                      (rate limit, preferences)
        │
        ▼
ConversationEngine.respond
   ├─ budget check (usage table)
   ├─ MoodEngine.get                   → persisted mood, decayed
   ├─ ConversationStore.recent         → last N messages
   ├─ MemoryStore.search               → top relevant facts
   ├─ build_system_prompt              → persona + mood + memory + JSON contract
   ├─ ProviderChain.complete           → primary, then fallbacks
   ├─ parse_turn_payload               → reply / mood / voice hint / memories
   ├─ MoodEngine.apply                 → hysteresis + persistence
   ├─ ConversationStore.add            → bounded context
   └─ MemoryStore.add / supersede      → dedupe, cap
        │
        ▼
VoiceDirector.plan                      (mode, frequency, cooldown, tags)
        │
        ├─ no  → send text (chunked to 1,900 chars)
        └─ yes → VoiceService
                   ├─ budget check (daily/monthly chars)
                   ├─ provider.synthesize  (cache by hash)
                   ├─ prepare_voice_note   (ffmpeg → Ogg/Opus, duration, waveform)
                   └─ send_voice_message   (3-step Discord REST voice message)
```

## Components

| Module | Responsibility | Deliberately does not |
| --- | --- | --- |
| `config.py` | Typed env settings, validation, problem reports | touch the network |
| `db.py` | Schema, WAL, migrations, usage/cooldown helpers | know about Discord |
| `security.py` | Owner auth, sanitising, in-memory rate limiting | store secrets |
| `ai/registry.py` | Provider construction + fallback order | build prompts |
| `ai/engine.py` | Prompt assembly, parsing, memory writes | speak to Discord |
| `personality/profile.py` | Character text and output contract | call the model |
| `mood/*` | Mood vocabulary, transitions, persistence | decide voice policy |
| `memory/*` | Facts, context window, extraction fallback | format prompts |
| `voice/director.py` | Whether/how to speak, cue selection | synthesise audio |
| `voice/providers/*` | Text → audio bytes | encode to Opus |
| `voice/audio.py` | ffmpeg conversion, duration, waveform | upload |
| `voice/sender.py` | Discord voice-message upload | decide anything |
| `voice/service.py` | Budgets, cache, delivery fallback | own a provider's HTTP |
| `images/*` | Identity assets, optional generation | pick random pictures |
| `cogs/*` | Command surface and embeds | contain business logic |

## Data model (SQLite, schema v1)

```
memories(id, scope_id, kind, subject, content, importance, fingerprint,
         source, created_at, updated_at, last_used_at, use_count)
         UNIQUE(scope_id, fingerprint)

messages(id, scope_id, role, content, channel_id, message_id, created_at)
         bounded: 80 rows per scope, 14 days

mood_state(scope_id PK, mood, intensity, reason, updated_at, expires_at, changes)
preferences(scope_id, user_id PK, voice_mode, voice_frequency, images_enabled)
settings(key PK, value, updated_at)          -- /setup channel whitelist
usage(period, kind, amount)                  -- day "2026-11-17", month "2026-11"
cooldowns(key PK, ends_at)                   -- voice notes, individual tags
meta(key PK, value)                          -- schema version
```

- `scope_id` is the owner user ID: memory and mood follow the person across DMs and
  servers.
- WAL mode + `synchronous=NORMAL` keeps writes durable enough for a host restart
  without hammering disk I/O (KataBump I/O is limited).
- Every query goes through `Database`, which serialises access on one connection
  and runs it in a worker thread — no `aiosqlite` dependency.

## Memory policy

- Model returns 0–3 `remember` items per turn, with kinds
  `fact|preference|person|date|plan|boundary|topic`.
- Fingerprint = SHA-1 of normalised `subject|content`; duplicates bump importance
  instead of creating rows.
- Contradictions use `supersedes_id`: the old row is deleted and the corrected one
  stored.
- Retrieval scores `0.55 × token coverage + 0.20 × importance + 0.12 × recency +
  0.06 × usage + subject bonus`, with weak matches filtered unless they are
  critical preference/boundary facts.
- The cap (default 400) prunes the least important, oldest rows.
- Extraction is a fallback only: when the reply envelope is missing, one extra
  cheap call extracts memories, so a plain-text provider still remembers things.

## Mood policy

- 11 moods, each with directives for energy, humour, verbosity, voice affinity,
  length and eligible audio cues.
- The model suggests a mood; a keyword heuristic fills in when it does not.
- A different mood needs intensity ≥ 0.30, and either 20 s dwell time, intensity
  ≥ 0.80, or a fresh baseline (so the first clear signal is not suppressed).
- Moods expire after 3–15 minutes depending on intensity and decay to `relaxed`.
- Persisted in SQLite: a restart does not erase her mood.

## Voice policy

- Modes: `off`, `request`, `occasional`, `always`.
- Occasional probability = base frequency × mood voice affinity × energy factor,
  capped at 0.9, with a cooldown (default 12 min) and a daily/monthly character
  budget.
- One synthesis request per note; no stitching of stock laughter clips.
- Audio tags are drawn from a per-mood whitelist, at most one lead + one breath
  cue, each with its own cooldown, and only for tag-capable models.
- If Ogg/Opus preparation fails, the audio goes out as a normal attachment rather
  than a broken voice note.

## Failure behaviour

| Failure | Behaviour |
| --- | --- |
| Primary LLM fails | Next provider in the chain; if all fail, a short apology |
| Rate limit hit | Silent ignore for messages too fast; explicit notice for commands |
| Daily AI budget hit | Polite "tomorrow" reply, no spend |
| TTS provider fails | Text reply still delivered; reason logged |
| ffmpeg missing | MP3 attachment fallback |
| Discord upload fails | Logged; no fake voice note sent |
| DB unavailable | Startup fails with a clear error instead of running half-alive |

## Extending

- **New chat provider:** implement `ChatProvider.complete`, add a branch in
  `ai/registry.build_provider`, add its env key to `PROVIDER_KEY_ENVS`.
- **New voice provider:** implement `VoiceProvider.synthesize`, expose
  `supports_audio_tags`, register in `voice/providers/registry.py`.
- **New memory ranking:** replace `MemoryStore.search`; nothing else assumes
  lexical scoring.
