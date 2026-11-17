"""Generate the bundled character portrait placeholders.

These are deliberately graphic identity marks — a crescent-and-star emblem on a
mood-tinted gradient — not fake faces and not commissioned art. Every variant
shares the same emblem, framing and palette family, so the repo ships one
consistent visual identity. Replace the files in ``assets/character/`` with real
artwork using the same names.

Run: python scripts/make_character_assets.py
"""

from __future__ import annotations

import binascii
import math
import struct
import zlib
from pathlib import Path

SIZE = 512
OUT_DIR = Path(__file__).resolve().parents[1] / "assets" / "character"

# Mood → (background top, background bottom, accent)
PALETTES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    "default": ((42, 33, 56), (92, 64, 82), (214, 168, 196)),
    "happy": ((53, 38, 48), (128, 84, 74), (245, 195, 130)),
    "playful": ((38, 32, 62), (104, 66, 108), (236, 160, 206)),
    "excited": ((56, 32, 44), (146, 78, 82), (255, 176, 132)),
    "relaxed": ((33, 42, 48), (80, 100, 92), (176, 212, 184)),
    "curious": ((30, 38, 58), (74, 92, 118), (162, 198, 232)),
    "sarcastic": ((40, 40, 44), (94, 92, 92), (206, 206, 198)),
    "sleepy": ((28, 30, 52), (66, 64, 96), (168, 164, 220)),
    "shy": ((54, 34, 44), (120, 78, 88), (240, 170, 178)),
    "annoyed": ((44, 34, 34), (92, 68, 62), (214, 150, 120)),
    "disappointed": ((30, 34, 44), (70, 78, 90), (150, 168, 188)),
    "serious": ((26, 28, 34), (58, 62, 72), (190, 196, 210)),
}

# Symbol geometry, identical for every mood so the identity is stable.
CRESCENT_MAIN_R = 118.0
CRESCENT_CUT_R = 101.0
CRESCENT_CUT_OFFSET = (44.0, -26.0)
STAR_CENTRE = (356.0, 150.0)
STAR_RADIUS = 30.0
RING_RADIUS = 176.0


def _clamp(value: float) -> int:
    return max(0, min(255, int(value)))


def _blend(base: tuple[float, float, float], colour: tuple[int, int, int], alpha: float) -> tuple[float, float, float]:
    alpha = max(0.0, min(1.0, alpha))
    return (
        base[0] * (1 - alpha) + colour[0] * alpha,
        base[1] * (1 - alpha) + colour[1] * alpha,
        base[2] * (1 - alpha) + colour[2] * alpha,
    )


def _coverage(distance: float, edge: float = 1.6) -> float:
    """Anti-aliased inside/outside coverage for a signed distance."""
    return max(0.0, min(1.0, (edge - distance) / (2 * edge) + 0.5))


def _render_pixel(x: float, y: float, palette) -> tuple[int, int, int]:
    top, bottom, accent = palette
    cx = cy = SIZE / 2
    t = y / (SIZE - 1)

    colour: tuple[float, float, float] = (
        top[0] + (bottom[0] - top[0]) * t,
        top[1] + (bottom[1] - top[1]) * t,
        top[2] + (bottom[2] - top[2]) * t,
    )

    # Warm glow behind the emblem.
    glow = math.hypot(x - cx, y - cy * 0.92) / (SIZE * 0.55)
    colour = _blend(colour, accent, max(0.0, 0.30 * (1 - glow)))

    # Soft halo disc.
    halo = abs(math.hypot(x - cx, y - cy) - RING_RADIUS * 0.78)
    if halo < 26:
        colour = _blend(colour, accent, 0.06 * (1 - halo / 26))

    # Crescent: main disc minus an offset disc.
    d_main = abs(math.hypot(x - cx, y - cy) - CRESCENT_MAIN_R)
    d_cut = abs(
        math.hypot(x - (cx + CRESCENT_CUT_OFFSET[0]), y - (cy + CRESCENT_CUT_OFFSET[1]))
        - CRESCENT_CUT_R
    )
    inside_main = _coverage(d_main, 1.8)
    inside_cut = _coverage(d_cut, 1.8)
    crescent_alpha = inside_main * (1 - inside_cut)
    if crescent_alpha > 0:
        # Subtle vertical sheen so the flat accent does not look like a sticker.
        sheen = 0.85 + 0.15 * max(0.0, 1 - (y - (cy - CRESCENT_MAIN_R)) / (2 * CRESCENT_MAIN_R))
        colour = _blend(colour, tuple(_clamp(c * sheen) for c in accent), 0.95 * crescent_alpha)

    # Four-point star.
    dx = abs(x - STAR_CENTRE[0])
    dy = abs(y - STAR_CENTRE[1])
    star_metric = (dx / STAR_RADIUS) ** 0.62 + (dy / STAR_RADIUS) ** 0.62
    star_alpha = 1.0 if star_metric <= 1.0 else 0.0
    if star_metric <= 1.12:
        star_alpha = max(0.0, min(1.0, (1.12 - star_metric) / 0.12))
    if star_alpha > 0:
        colour = _blend(colour, accent, 0.9 * star_alpha)

    # Thin ring, brightest on the upper left.
    ring = abs(math.hypot(x - cx, y - cy) - RING_RADIUS)
    if ring < 1.5:
        angle_weight = max(0.0, 1.0 - math.hypot(x - cx + 60, y - cy + 60) / (SIZE * 0.75))
        colour = _blend(colour, accent, 0.30 + 0.35 * angle_weight)

    # Vignette.
    edge = math.hypot(x - cx, y - cy) / (SIZE * 0.78)
    colour = _blend(colour, (8, 6, 10), max(0.0, (edge - 0.55) * 0.95))

    return _clamp(colour[0]), _clamp(colour[1]), _clamp(colour[2])


def render(name: str) -> bytes:
    palette = PALETTES[name]
    pixels = bytearray(SIZE * SIZE * 3)
    for y in range(SIZE):
        row = y * SIZE * 3
        for x in range(SIZE):
            r, g, b = _render_pixel(x + 0.5, y + 0.5, palette)
            index = row + x * 3
            pixels[index] = r
            pixels[index + 1] = g
            pixels[index + 2] = b
    return bytes(pixels)


def write_png(path: Path, rgb: bytes, width: int = SIZE, height: int = SIZE) -> None:
    raw = b"".join(
        b"\x00" + rgb[y * width * 3 : (y + 1) * width * 3] for y in range(height)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in PALETTES:
        target = OUT_DIR / f"leyla_{name}.png"
        write_png(target, render(name))
        print(f"wrote {target.relative_to(OUT_DIR.parent.parent)} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
