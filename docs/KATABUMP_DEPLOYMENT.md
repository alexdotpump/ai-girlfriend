# Deploying Leyla to KataBump

KataBump runs Discord bots on Node.js or Python. The free plan is **308 MB RAM,
716 MB disk, 25% CPU**, and must be **renewed every 4 days**. Leyla is built for
that: `requirements.txt` has three runtime packages, storage is SQLite on disk, and
a fully initialised process measured **~50 MB RSS** in this build.

## 1. Create the server

1. Sign in at <https://dashboard.katabump.com> → **Create a server**.
2. Choose **Python**, give it a name, create it.
3. Open <https://control.katabump.com> → your server → **Startup**:
   - **PY FILE**: `main.py`
   - **Python version**: 3.11 (3.10 also works; the code is parsed as 3.10)
   - Leave *additional packages* empty — `requirements.txt` is installed
     automatically on start (`pip install -r requirements.txt`).

## 2. Upload the project

`main.py` and `requirements.txt` **must be at the server root** (not inside a
subfolder), together with `leyla/` and `assets/`.

**Web upload**

```bash
git clone <your repo> leyla && cd leyla
rm -rf .git data .venv __pycache__
zip -r leyla.zip . -x '*.pyc' -x '__pycache__/*' -x 'data/*' -x '.venv/*'
```

Upload `leyla.zip` in the **Files** tab (100 MB per file limit), then unzip it in
the panel. Confirm the root contains `main.py`, `requirements.txt`, `leyla/`,
`assets/`, `docs/`.

**SFTP** (no size limit): use the credentials from **Files → SFTP**, then upload
the contents of the repo root. Never upload `.venv/` or `data/`.

## 3. Configure `.env`

Create `.env` at the server root (upload it — do not paste secrets into chat or
commit them):

```dotenv
DISCORD_TOKEN=...
OWNER_DISCORD_ID=...
DISCORD_GUILD_ID=...          # optional, instant command sync
GEMINI_API_KEY=...            # free: https://aistudio.google.com/apikey
AI_MODEL=gemini-flash-latest
AI_MODEL_FALLBACKS=gemini-3.5-flash,gemini-flash-lite-latest
GROQ_API_KEY=...             # voice: https://console.groq.com/keys
GROQ_TTS_VOICE=autumn
VOICE_MODE=occasional
```

Every other variable has a default; see [.env.example](../.env.example). If you
only want text chat at first, set `VOICE_MODE=off` — it also removes the required
Groq key from startup validation.

## 4. ffmpeg (needed for real voice notes)

Check the console:

```bash
ffmpeg -version
```

- **Present** → nothing to do; `FFMPEG_PATH` can stay empty.
- **Missing** → voice notes cannot be encoded to Ogg/Opus. Two options:
  1. Upload a **static Linux x86_64 ffmpeg build** to the project (e.g.
     `bin/ffmpeg`, chmod +x in the console) and set
     `FFMPEG_PATH=/home/container/bin/ffmpeg` — or the absolute path shown by
     `pwd`.
  2. Leave it missing: Leyla still sends the generated audio as a normal playable
     MP3 attachment instead of a native voice message. Nothing breaks; she is just
     not a voice note.

## 5. Start and verify

1. **Start** the server. First boot installs dependencies, creates
   `data/leyla.sqlite3`, syncs slash commands, then logs
   `connected as Leyla#....`.
2. In Discord: `/help` → `/status` → `/voice test`.
3. If `/voice test` sends a playable note with a waveform, voice is working
   end-to-end. If it replies "couldn't send it: …", the reason is in the reply and
   the full error is in the console logs.
4. Chat: DM Leyla, mention her in a server, or `/setup channel add` in a private
   channel.

## 6. Operations

| Task | How |
| --- | --- |
| **Renew the free server** | Every 4 days in the dashboard. Missing it suspends the server for one day, then **deletes it and its files**. Set a reminder. |
| Restart | Panel **Restart**. Memories, mood, preferences, usage counters and the voice cache persist; commands re-sync. |
| Backup | Download `data/leyla.sqlite3` via SFTP. That single file is the whole personality history. Copy it while the bot is stopped (or accept WAL) to be safe. |
| Restore | Upload the file back to `data/` and restart. |
| Logs | Panel console. Optionally set `LOG_FILE=logs/leyla.log` (rotated at 1 MB × 2) — the free disk is small. |
| Update code | Upload changed files, keep `.env` and `data/`, restart. |
| Disk control | `data/voice_cache/` is pruned to 25 MB; generated images are individual files under `data/images/` (delete old ones). The database stays small (context is capped). |
| Change model | Edit `AI_PROVIDER`/`AI_MODEL` (and key), restart. No code change. |

## 7. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `ModuleNotFoundError: discord` | `requirements.txt` missing from root, or wrong PY FILE. Restart to trigger install. |
| `configuration problem: DISCORD_TOKEN is required` | `.env` not at root, or wrong filename (`.env.txt`). Upload `.env` exactly. |
| `Improper token has been passed` | Token reset/copied wrong. Reset it in the Developer Portal and update `.env`. |
| Bot online, slash commands missing | Set `DISCORD_GUILD_ID` and restart; or re-invite with the `applications.commands` scope. Global sync can take an hour. |
| Bot ignores messages in a server | She only answers DMs, mentions, or whitelisted channels. `/setup channel add`. |
| No replies to anyone | Check `OWNER_DISCORD_ID` — non-owners are ignored silently by design. |
| Voice: `ffmpeg is required…` | Install/point `FFMPEG_PATH`, or accept the MP3 attachment fallback. |
| Voice: `synthesis failed: … 401` | Bad/expired `GROQ_API_KEY` (or `ELEVENLABS_API_KEY` if you switched providers). |
| Voice: `Groq TTS failed: … 429` | Groq rate limit. Lower `VOICE_FREQUENCY`, raise `VOICE_COOLDOWN_MINUTES`; text replies still work. |
| Voice: `daily TTS budget` | Character budget reached. Raise `TTS_DAILY_CHAR_LIMIT` only if your plan allows. |
| AI: `all AI providers failed` | Bad key, exhausted free quota, or model ID retired. Check `/status` and the logs; free model names change. `gemini-flash-latest` answering 503 "high demand" is the common case — keep `AI_MODEL_FALLBACKS` set. |
| `database is locked` | Another process opened the DB. Run one instance only. |
| Server killed / OOM | Free plan is 308 MB. Keep `data/images` small, don't add heavy packages; measured usage is ~50 MB. |
| Commands respond "this one is private." | `OWNER_DISCORD_ID` does not match your account. |

## 8. What persists across restarts

- SQLite: memories, bounded context, mood, preferences, usage, cooldowns, channel
  whitelist.
- Filesystem: voice cache (up to 25 MB), generated images, optional log file.
- Recreated on boot: providers, HTTP session, Discord connection, command sync.

Nothing important lives in memory only except the per-process rate limiter and the
message dedupe ring, both of which rebuild harmlessly.

## 9. Free-plan limits to respect

- 308 MB RAM · 716 MB disk · 25% CPU · 100 I/O ops/s.
- No managed database on the free plan — hence SQLite.
- Uploads: 100 MB per file via web, unlimited via SFTP.
- 4-day renewal. Paid plans (Starter 1,512 MB / 4 GB) remove the renewal and give
  more headroom, but Leyla does not need them.
