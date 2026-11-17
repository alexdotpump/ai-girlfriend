# Character assets

These twelve PNGs are **procedural placeholders**, not commissioned art. They share
one silhouette, framing and palette family so Leyla has a consistent visual
identity out of the box; each mood variant changes the lighting and accent colour.

Replace them with your own character art using the same file names. Keep the same
face/design across all files — the bot only ever sends these images and always
describes them as Leyla.

| File | Used for |
| --- | --- |
| `leyla_default.png` | Profile picture, fallback for every mood |
| `leyla_happy.png` | happy |
| `leyla_playful.png` | playful |
| `leyla_excited.png` | excited |
| `leyla_relaxed.png` | relaxed |
| `leyla_curious.png` | curious |
| `leyla_sarcastic.png` | sarcastic |
| `leyla_sleepy.png` | sleepy |
| `leyla_shy.png` | shy |
| `leyla_annoyed.png` | annoyed |
| `leyla_disappointed.png` | disappointed |
| `leyla_serious.png` | serious |

- Supported formats: `.png`, `.jpg`, `.jpeg`, `.webp` (same base name).
- 512×512 or larger, square, is ideal.
- Missing mood files fall back to `leyla_default.png`.
- Regenerate the placeholders with `python scripts/make_character_assets.py`.

Do not add images of real people or explicit content. The optional image generator
uses the same identity description and refuses unsafe prompts.
