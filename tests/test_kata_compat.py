"""KataBump compatibility: runtime version, entry point, env docs, no stray hosts."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from leyla.config import PROVIDER_KEY_ENVS

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "venv", "data", "__pycache__", ".pytest_cache", "node_modules"}


def project_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def test_entry_point_and_requirements_are_at_root():
    assert (ROOT / "main.py").is_file()
    requirements = (ROOT / "requirements.txt").read_text("utf-8")
    assert "discord.py" in requirements
    assert "python-dotenv" in requirements
    assert "aiohttp" in requirements
    # Heavy/optional packages must not be forced on a 308 MB host.
    for heavy in ("torch", "tensorflow", "numpy", "pandas", "playwright"):
        assert heavy not in requirements.lower()


def test_python_sources_parse_as_python_310():
    """KataBump recommends Python 3.10/3.11 — no 3.12-only syntax allowed."""
    parsed = 0
    for path in project_files():
        if path.suffix != ".py":
            continue
        source = path.read_text("utf-8")
        try:
            ast.parse(source, filename=str(path), feature_version=(3, 10))
        except SyntaxError as exc:  # pragma: no cover - failure detail
            pytest.fail(f"{path.relative_to(ROOT)} is not Python 3.10 compatible: {exc}")
        parsed += 1
    assert parsed > 20


def test_env_example_documents_every_variable():
    example = (ROOT / ".env.example").read_text("utf-8")
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", example, flags=re.MULTILINE))

    source = (ROOT / "leyla" / "config.py").read_text("utf-8")
    used = set(
        re.findall(
            r'(?:get|_bool|_int|_float|_int_set|_csv)\(\s*env\s*,\s*"([A-Z0-9_]+)"', source
        )
    )
    used |= set(re.findall(r'(?<![\w.])get\("([A-Z][A-Z0-9_]{2,})"', source))
    for keys in PROVIDER_KEY_ENVS.values():
        used |= set(keys)

    assert used, "env scanner found nothing — the regex or config changed"
    missing = sorted(used - documented)
    assert not missing, f"undocumented environment variables: {missing}"


def test_required_service_keys_are_documented():
    example = (ROOT / ".env.example").read_text("utf-8")
    for key in (
        "DISCORD_TOKEN",
        "OWNER_DISCORD_ID",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "FFMPEG_PATH",
        "IMAGE_PROVIDER",
    ):
        assert f"{key}=" in example


def test_no_forbidden_hosting_configuration():
    offenders = []
    for path in project_files():
        if path.suffix not in {".py", ".md", ".toml", ".txt", ".json", ".yaml", ".yml", ".example"}:
            continue
        if path.name == "test_kata_compat.py":
            continue  # this file names the forbidden host in order to assert it is absent
        try:
            text = path.read_text("utf-8").lower()
        except UnicodeDecodeError:
            continue
        if "railway" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_main_fails_cleanly_without_credentials(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT), "HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )
    assert result.returncode == 2
    assert "DISCORD_TOKEN" in result.stderr


def test_character_assets_are_present_and_consistent():
    assets = ROOT / "assets" / "character"
    assert (assets / "leyla_default.png").is_file()
    pngs = sorted(assets.glob("leyla_*.png"))
    assert len(pngs) >= 10
    # Every bundled portrait is a real PNG of the same size (one identity).
    signature = pngs[0].read_bytes()[:8]
    assert signature == b"\x89PNG\r\n\x1a\n"
    import struct

    sizes = set()
    for png in pngs:
        data = png.read_bytes()
        width, height = struct.unpack(">II", data[16:24])
        sizes.add((width, height))
    assert sizes == {(512, 512)}
