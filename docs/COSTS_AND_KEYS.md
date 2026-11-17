# Costs, free tiers and API keys

Pricing was checked in **November 2026** from the providers' own pages and public
rate-limit trackers. Free tiers change without notice — re-check before relying on
"free". Anything marked *verified* was confirmed during this build.

## Default stack: €0/month

| Service | Role | Free? | Verified limits | Key |
| --- | --- | --- | --- | --- |
| Discord | Bot platform | Yes | — | `DISCORD_TOKEN` |
| Google Gemini API | Conversation brain | Yes, no card | Free of charge for Flash / Flash-Lite models; per-project limits are shown in AI Studio and reset daily (not published, not guaranteed) | `GEMINI_API_KEY` |
| Groq Orpheus TTS | Expressive voice notes | Paid per use | $22 per 1M characters ≈ **$0.004 per 190-character note**; preview model, limits can change | `GROQ_API_KEY` |
| KataBump | Hosting | Yes | 308 MB RAM · 716 MB disk · 25% CPU · renewal every 4 days | — |
| SQLite / ffmpeg / edge-tts / Pollinations | Storage and fallbacks | Yes | SQLite local; `edge-tts` free and keyless; Pollinations keyless and rate-limited | — |

Starter configuration therefore costs **€0** as long as you stay inside those
budgets. Leyla enforces its own budgets (`AI_DAILY_REQUEST_LIMIT`,
`TTS_DAILY_CHAR_LIMIT`, `TTS_MONTHLY_CHAR_LIMIT`, `IMAGE_DAILY_LIMIT`) so it cannot
silently overspend.

## Alternative brains

| Provider | Free tier | Notes |
| --- | --- | --- |
| **Gemini** (default) | Free Flash models | Free-tier prompts may be used to improve Google products; use a paid key if that is a problem. `gemini-2.5-flash` was deprecated in November 2026 — prefer `gemini-flash-latest` or `gemini-3.8-flash`. |
| **Groq** | 30 req/min · 1,000 req/day · 8k tokens/min · 200k tokens/day per model | Free text models: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`. Llama models left the free tier in August 2026. |
| **OpenRouter** | 20 req/min, 50/day (1,000/day after $10 purchased credits) | Free model IDs end in `:free`; set `AI_MODEL` explicitly. |
| **OpenAI** | No free tier | Paid, high quality. |
| **Custom** | Depends on you | Any OpenAI-compatible endpoint via `AI_BASE_URL`. |
| **`echo`** | Always free | Offline smoke-test provider. Not a conversational model. |

## Voice options

| Provider | Free? | Expression support | Recommendation |
| --- | --- | --- | --- |
| **Groq Orpheus** (`canopylabs/orpheus-v1-english`) | No, $22/1M chars | Nonverbal tokens `<laugh>`, `<chuckle>`, `<sigh>` (plus bracketed directions) | **Default.** Verified live: tokens are performed, never read aloud. |
| **ElevenLabs v4 / v3** | 10k credits/mo free | **Audio tags**: `[laughs]`, `[laughs harder]`, `[starts laughing]`, `[sighs]`, `[exhales]`, `[clears throat]`, `[whispers]`, `[excited]`, `[curious]`, `[sarcastic]`, `[mischievously]`, `[nervous laugh]`, `[snorts]` | Optional alternative (`VOICE_PROVIDER=elevenlabs`); free plan is non-commercial. |
| ElevenLabs Starter | $6/mo (first month $1) | Same + commercial licence | 30k credits/month; get this if you publish audio. |
| **OpenAI TTS** (`gpt-4o-mini-tts`) | No | Delivery via natural-language `instructions` only, no laughter/breaths | Good cheaper alternative; less expressive. |
| **edge-tts** | Yes, keyless | Prosody/punctuation only | Zero-cost fallback; noticeably more "TTS". |
| Pollinations images | Yes, keyless | n/a | Optional image generation. |
| OpenAI images (`gpt-image-1`) | No | n/a | Optional, disabled by default. |

## Rough monthly cost by usage

Assumptions: ~100 messages/day, ~60% of replies short.

| Scenario | Monthly |
| --- | --- |
| Gemini free + voice off + KataBump free | **€0** |
| Gemini free + Groq Orpheus (a few 190-char notes a day) | **< $1** |
| Gemini free + ElevenLabs free | **€0** (within 10k characters/month) |
| Gemini free + ElevenLabs Starter (2–3 notes/day) | **$6** |
| OpenAI brain + OpenAI TTS, 100 msgs/day | a few dollars; depends on model and note length |

Cost controls already in the code:

- one LLM request per turn (reply + mood + memory + voice hint in one envelope)
- context capped at `CHAT_HISTORY_TURNS` messages and 6 memories per prompt
- `AI_MAX_OUTPUT_TOKENS` default 320
- TTS cache by text hash; identical notes are not regenerated
- voice cooldown + per-tag cooldowns
- daily/monthly character and request budgets
- image generation off unless explicitly enabled

## Keys you need

**Required**

| Variable | Where to get it |
| --- | --- |
| `DISCORD_TOKEN` | Discord Developer Portal → your app → Bot → Reset Token |
| `OWNER_DISCORD_ID` | Your Discord user ID (Developer Mode → right-click yourself) |
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> (free, no card) |
| `GROQ_API_KEY` | <https://console.groq.com/keys> (also used for chat fallback if configured) |
| `GROQ_TTS_VOICE` | `autumn` (female, default) · `diana` · `hannah` |

**Optional**

`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `AI_API_KEY`, `AI_BASE_URL`,
`AI_MODEL_FALLBACKS`, `DISCORD_GUILD_ID`, `ALLOWED_CHANNEL_IDS`, `FFMPEG_PATH`,
`GROQ_TTS_MODEL`, `ELEVENLABS_API_KEY`/`ELEVENLABS_VOICE_ID`, `OPENAI_TTS_*`,
`IMAGE_PROVIDER`/`OPENAI_IMAGE_MODEL`.

If you set `VOICE_MODE=off`, no voice key is required at all.

## Voice identity and cloning

Leyla speaks with Groq's Orpheus voice `autumn`, a stock voice persona — no cloning
involved. Do **not** clone a real person's voice or imitate a public figure. If you
switch to ElevenLabs, they require attribution ("elevenlabs.io" / "11.ai") when you
publish audio generated on their free plan.

## Security rules for keys

- Keys live in `.env` on the host, never in git, never in logs.
- `logging_setup.RedactionFilter` scrubs `Bot <token>`, `sk-…`, `AIza…`, and
  Discord-token patterns from every log record.
- `.gitignore` excludes `.env*` (except `.env.example`) and `data/`.
- The only place a token is read for voice uploads is the bot's own HTTP client at
  call time; it is never printed or returned.
