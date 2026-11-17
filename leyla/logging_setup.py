"""Logging setup.

Two rules:
1. Never log credentials — :class:`RedactionFilter` scrubs anything that looks
   like a token or API key even if a library includes it in an error.
2. Keep output small — KataBump's free plan has limited disk.
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_PATTERNS = (
    re.compile(r"(?i)(bot\s+)[A-Za-z0-9._-]{20,}"),
    re.compile(r"(?i)\b(sk|rk|pk)-[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*)[\"']?[^\s,\"'}]{8,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,}\b"),
)


def redact(text: str) -> str:
    """Remove anything that looks like a credential from ``text``."""
    if not text:
        return text
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(lambda m: f"{m.group(1)}[redacted]" if m.groups() else "[redacted]", cleaned)
    return cleaned


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        try:
            # Only strings are touched: coercing numeric args to str would break
            # %-style formatting such as "%.1fs".
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {
                    key: redact(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
        except Exception:  # noqa: BLE001 - logging must never raise
            pass
        return True


_CONFIGURED = False


def setup_logging(level: str = "INFO", log_file: str | None = None, *, force: bool = False) -> None:
    """Configure root logging once per process."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, (level or "INFO").upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream = logging.StreamHandler(stream=sys.stdout)
    stream.setFormatter(formatter)
    stream.addFilter(RedactionFilter())
    root.addHandler(stream)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RedactionFilter())
        root.addHandler(file_handler)

    # discord.py is chatty; keep it at WARNING unless we are debugging.
    logging.getLogger("discord").setLevel(logging.INFO if level.upper() == "DEBUG" else logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger (``leyla.*`` namespace)."""
    return logging.getLogger(name if name.startswith("leyla") else f"leyla.{name}")
