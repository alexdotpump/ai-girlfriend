"""Leyla — entry point.

KataBump runs this file (Startup → PY FILE = ``main.py``). It loads ``.env``,
validates the configuration and hands control to :class:`leyla.bot.LeylaBot`.
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from leyla.bot import LeylaBot
from leyla.config import Settings
from leyla.errors import ConfigError
from leyla.logging_setup import get_logger, setup_logging


def main() -> int:
    load_dotenv()
    settings = Settings.load()

    setup_logging(settings.log_level, settings.log_file)
    log = get_logger("leyla.main")

    problems = settings.problems()
    if problems:
        for problem in problems:
            log.error("configuration problem: %s", problem)
        print(
            "Leyla cannot start — fix the configuration problems above:\n  - "
            + "\n  - ".join(problems)
            + "\n\nCopy .env.example to .env and fill in the required values.",
            file=sys.stderr,
        )
        return 2

    for warning in settings.warnings():
        log.warning("config: %s", warning)

    log.info(
        "starting Leyla | ai=%s/%s voice=%s mode=%s images=%s",
        settings.ai_provider,
        settings.ai_model,
        settings.voice_provider,
        settings.voice_mode,
        settings.image_provider if settings.image_generation_enabled else "disabled",
    )

    bot = LeylaBot(settings)
    try:
        bot.run(settings.discord_token, log_handler=None)
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        log.info("shutdown requested")
    except ConfigError as exc:
        log.error("startup failed: %s", exc)
        return 2
    finally:
        try:
            asyncio.run(bot.shutdown())
        except Exception:  # noqa: BLE001 - best-effort cleanup
            log.debug("cleanup during shutdown failed", exc_info=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
