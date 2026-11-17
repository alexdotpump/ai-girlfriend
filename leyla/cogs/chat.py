"""Conversational surfaces: free chat and /chat."""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from ..chat_flow import run_turn
from ..errors import UnauthorizedError
from ..logging_setup import get_logger
from ..security import is_owner
from ..utils.text import human_duration

log = get_logger("leyla.cogs.chat")


class ChatCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        owner_ids = getattr(self.bot.settings, "owner_ids", frozenset())
        if not is_owner(interaction.user.id if interaction.user else None, owner_ids):
            raise UnauthorizedError("non-owner interaction rejected")
        return True

    # ---------------------------------------------------------------- /chat
    @app_commands.command(name="chat", description="Talk to Leyla")
    @app_commands.describe(
        message="What you want to say (leave empty to just open the conversation)",
        voice="Answer with a voice note instead of text",
    )
    async def chat(
        self,
        interaction: discord.Interaction,
        message: str | None = None,
        voice: bool = False,
    ) -> None:
        assert self.bot.services is not None
        services = self.bot.services
        await interaction.response.defer(thinking=True)

        text = (message or "").strip()
        if not text:
            mood = await services.mood.get(str(interaction.user.id))
            await interaction.followup.send(
                f"{mood.spec.emoji} I'm here. what's on your mind?",
                ephemeral=False,
            )
            return

        scope = str(interaction.user.id)
        channel = interaction.channel or await interaction.user.create_dm()

        async def send(part: str) -> None:
            await interaction.followup.send(part, ephemeral=False)

        try:
            async with channel.typing():
                outcome = await run_turn(
                    services=services,
                    bot=self.bot,
                    channel=channel,
                    sender=send,
                    scope_id=scope,
                    user_text=text,
                    display_name=interaction.user.display_name,
                    channel_id=getattr(channel, "id", None),
                    explicit_voice=voice,
                )
        except Exception as exc:  # noqa: BLE001 - surfaced through tree.on_error
            log.warning("/chat failed: %s", exc)
            raise

        if voice and not outcome.voice_sent:
            reason = (outcome.voice_reason or "voice unavailable").strip()
            await interaction.followup.send(
                f"*couldn't make the voice note — {reason[:180]}*", ephemeral=True
            )

    # ---------------------------------------------------------- /mood view
    @app_commands.command(name="mood", description="See how Leyla is feeling right now")
    async def mood(self, interaction: discord.Interaction) -> None:
        assert self.bot.services is not None
        state = await self.bot.services.mood.get(str(interaction.user.id))
        spec = state.spec
        embed = discord.Embed(
            title=f"{spec.emoji} {spec.label}",
            description=state.reason or "no particular reason",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="intensity", value=f"{int(state.intensity * 100)}%")
        embed.add_field(name="energy", value=f"{int(spec.energy * 100)}%")
        embed.add_field(name="mood shifts", value=str(state.changes))
        embed.set_footer(
            text=f"fades back to relaxed in {human_duration(state.expires_at - time.time())}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
