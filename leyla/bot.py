"""The Discord client: connection, routing, command registration, shutdown."""

from __future__ import annotations

import time
from collections import deque

import discord
from discord import app_commands
from discord.ext import commands

from .chat_flow import run_turn
from .config import Settings
from .errors import (
    BudgetExceededError,
    LeylaError,
    ProviderError,
    RateLimitedError,
    UnauthorizedError,
    VoiceError,
)
from .logging_setup import get_logger, setup_logging
from .security import is_owner
from .services import Services
from .utils.text import strip_bot_mention

log = get_logger("leyla.bot")

MAX_DEDUPE = 400


class LeylaBot(commands.Bot):
    """Private companion bot.

    Privacy model: every DM and every message event is checked against
    ``settings.owner_ids`` before any model call happens. Non-owners get silence,
    not an error, so the bot never becomes a public service.
    """

    def __init__(self, settings: Settings, *, sync_commands: bool = True) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        intents.guild_messages = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
            activity=discord.Activity(type=discord.ActivityType.listening, name="you"),
            status=discord.Status.online,
        )
        self.settings = settings
        self.sync_commands = sync_commands
        self.services: Services | None = None
        self._seen_ids: deque[int] = deque(maxlen=MAX_DEDUPE)
        self._seen_set: set[int] = set()
        self.started_at = time.time()

    # ------------------------------------------------------------- lifecycle
    async def setup_hook(self) -> None:
        setup_logging(self.settings.log_level, self.settings.log_file)
        self.services = await Services.create(self.settings)

        await self.add_cog(ChatCog(self))
        await self.add_cog(AdminCog(self))
        self.tree.on_error = self.on_app_command_error

        if self.sync_commands:
            await self._sync_commands()

        log.info(
            "setup complete: %d commands registered",
            len(self.tree.get_commands()),
        )

    async def _sync_commands(self) -> None:
        try:
            if self.settings.guild_id:
                guild = discord.Object(id=self.settings.guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
            else:
                synced = await self.tree.sync()
            log.info("synced %d application commands", len(synced))
        except discord.HTTPException as exc:
            log.error("command sync failed: %s", exc)

    async def on_ready(self) -> None:
        user = self.user
        log.info(
            "connected as %s (id=%s) in %d guilds",
            user,
            getattr(user, "id", "?"),
            len(self.guilds),
        )

    async def on_disconnect(self) -> None:
        log.warning("gateway disconnected; discord.py will reconnect automatically")

    async def on_resumed(self) -> None:
        log.info("gateway session resumed")

    async def close(self) -> None:
        log.info("shutting down")
        if self.services is not None:
            await self.services.aclose()
        await super().close()

    async def shutdown(self) -> None:
        """Idempotent cleanup used by main() after run() returns."""
        if self.services is not None:
            await self.services.aclose()

    # -------------------------------------------------------------- routing
    def _scope(self, user_id: int) -> str:
        # Owner-scoped: Leyla's memory follows the person, not the channel.
        return str(user_id)

    def _remember(self, message_id: int) -> bool:
        """Return False for a duplicate delivery."""
        if message_id in self._seen_set:
            return False
        if len(self._seen_ids) == self._seen_ids.maxlen:
            oldest = self._seen_ids[0]
            self._seen_set.discard(oldest)
        self._seen_ids.append(message_id)
        self._seen_set.add(message_id)
        return True

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or (self.user and message.author.id == self.user.id):
            return
        if not is_owner(message.author.id, self.settings.owner_ids):
            return
        if self.services is None:
            return

        if not await self._channel_allowed(message):
            return

        text = strip_bot_mention(message.content, self.user.id if self.user else None)
        if not text:
            return

        if not self._remember(message.id):
            log.debug("duplicate message %s ignored", message.id)
            return

        scope = self._scope(message.author.id)
        if await self.services.conversations.seen_message(scope, message.id):
            log.debug("already-processed message %s ignored", message.id)
            return

        async def send(part: str) -> None:
            await message.reply(part, mention_author=False, allowed_mentions=discord.AllowedMentions.none())

        try:
            async with message.channel.typing():
                await run_turn(
                    services=self.services,
                    bot=self,
                    channel=message.channel,
                    sender=send,
                    scope_id=scope,
                    user_text=text,
                    display_name=message.author.display_name,
                    channel_id=message.channel.id,
                    message_id=message.id,
                )
        except RateLimitedError as exc:
            log.info("rate limited: %s", exc)
        except (ProviderError, BudgetExceededError, VoiceError) as exc:
            log.warning("chat failed: %s", exc)
            try:
                await message.channel.send(
                    "something glitched on my end — give me a second and try again.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                log.debug("could not send failure notice", exc_info=True)
        except discord.HTTPException as exc:
            log.error("discord error while replying: %s", exc)

    async def _channel_allowed(self, message: discord.Message) -> bool:
        if message.guild is None:
            return True  # DMs are always private to the owner
        assert self.services is not None
        configured = await self.services.runtime.allowed_channels(message.guild.id)
        if configured:
            return message.channel.id in configured
        if self.settings.allowed_channel_ids:
            return message.channel.id in self.settings.allowed_channel_ids
        # Safe default in shared servers: only answer when mentioned.
        return bool(self.user and self.user in message.mentions)

    # ---------------------------------------------------------- error paths
    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, UnauthorizedError):
            log.warning("unauthorised command use by %s", interaction.user)
            message = "this one is private."
        elif isinstance(original, RateLimitedError):
            message = "slow down a little — I need a second between messages."
        elif isinstance(original, BudgetExceededError):
            message = "I've hit today's usage limit. Let's pick this up tomorrow?"
        elif isinstance(original, VoiceError):
            message = "my voice is acting up — try text instead."
        elif isinstance(original, ProviderError):
            message = "my thoughts got stuck for a second. Try again?"
        elif isinstance(original, LeylaError):
            message = getattr(original, "public_message", "something went wrong on my side.")
        else:
            message = "something went wrong on my side."
            log.error("unhandled command error", exc_info=original)

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            log.debug("could not deliver error message", exc_info=True)


# Imported late to avoid a circular import at module load time.
from .cogs.chat import ChatCog  # noqa: E402
from .cogs.commands import AdminCog  # noqa: E402

__all__ = ["LeylaBot"]
