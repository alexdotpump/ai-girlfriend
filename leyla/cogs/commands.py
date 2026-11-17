"""Owner commands: help, setup, voice, memory, status, photo."""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from ..config import VOICE_MODES
from ..errors import ImageError, UnauthorizedError, VoiceError
from ..logging_setup import get_logger
from ..security import is_owner, parse_memory_id
from ..utils.text import human_duration, truncate

log = get_logger("leyla.cogs.commands")


class AdminCog(commands.Cog):
    """Everything private or administrative. Owner-gated at the cog level."""

    voice_group = app_commands.Group(name="voice", description="Configure Leyla's voice notes")
    memory_group = app_commands.Group(name="memory", description="Manage what Leyla remembers")
    setup_group = app_commands.Group(name="setup", description="Set up Leyla in this server")

    def __init__(self, bot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        owner_ids = getattr(self.bot.settings, "owner_ids", frozenset())
        if not is_owner(interaction.user.id if interaction.user else None, owner_ids):
            raise UnauthorizedError("non-owner interaction rejected")
        return True

    @property
    def services(self):
        assert self.bot.services is not None
        return self.bot.services

    def scope(self, interaction: discord.Interaction) -> str:
        return str(interaction.user.id)

    # ------------------------------------------------------------------ help
    @app_commands.command(name="help", description="What Leyla can do")
    async def help_command(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Leyla",
            description=(
                "Private companion. Talk to her in DMs, when you mention her, or in a channel "
                "you whitelist with `/setup`."
            ),
            colour=discord.Colour.from_str("#c9a0dc"),
        )
        embed.add_field(
            name="Conversation",
            value="`/chat` · `/mood` · `/photo`",
            inline=False,
        )
        embed.add_field(
            name="Voice",
            value="`/voice mode` · `/voice frequency` · `/voice test` · `/voice status`",
            inline=False,
        )
        embed.add_field(
            name="Memory",
            value="`/memory list` · `/memory add` · `/memory search` · `/memory reset`\n"
            "`/memory-delete` · `/memory-clear`",
            inline=False,
        )
        embed.add_field(name="Server", value="`/setup` · `/status`", inline=False)
        embed.set_footer(text="Ask for a voice note any time — or just say 'send me a voice note'.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ status
    @app_commands.command(name="status", description="Show Leyla's runtime status")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        s = self.services
        usage = await s.usage_snapshot()
        memories = await s.memories.count(self.scope(interaction))
        context = await s.conversations.stats(self.scope(interaction))
        mood = await s.mood.get(self.scope(interaction))
        try:
            db_size = s.settings.db_path.stat().st_size / 1024
        except OSError:
            db_size = 0.0
        rss = _rss_mb()

        embed = discord.Embed(title="Leyla · status", colour=discord.Colour.green())
        embed.add_field(
            name="brain",
            value=f"{' → '.join(s.chain.names)}\n`{s.settings.ai_model}`",
            inline=True,
        )
        embed.add_field(
            name="voice",
            value=(
                f"{s.settings.voice_provider} · mode `{s.settings.voice_mode}`\n"
                f"ffmpeg: {'yes' if s.voice.ffmpeg_available else 'missing'}\n"
                f"tts chars today {usage['tts_today']}/{usage['tts_daily_limit']}"
            ),
            inline=True,
        )
        embed.add_field(
            name="mood",
            value=mood.describe(),
            inline=True,
        )
        embed.add_field(
            name="memory",
            value=f"{memories} memories · {context['count']} recent messages",
            inline=True,
        )
        embed.add_field(
            name="usage today",
            value=(
                f"llm {usage['llm_today']}/{usage['llm_limit']} requests\n"
                f"images {usage['images_today']}/{usage['images_limit']}\n"
                f"tts month {usage['tts_month']}/{usage['tts_monthly_limit']}"
            ),
            inline=True,
        )
        embed.add_field(
            name="process",
            value=(
                f"uptime {human_duration(time.time() - s.started_at)}\n"
                f"db {db_size:.0f} KB · rss {rss}\n"
                f"guilds {len(self.bot.guilds)} · latency {self.bot.latency * 1000:.0f} ms"
            ),
            inline=True,
        )
        embed.set_footer(text=f"image generation: {'on' if s.images.generation_enabled else 'off'}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ setup
    @setup_group.command(name="show", description="Show the current server setup")
    async def setup_show(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This is a DM — server setup only applies in guilds.", ephemeral=True
            )
            return
        channels = await self.services.runtime.allowed_channels(interaction.guild.id)
        prefs = await self.services.preferences.get(self.scope(interaction), self.scope(interaction))
        value = (
            ", ".join(f"<#{cid}>" for cid in sorted(channels))
            if channels
            else "none — Leyla only answers when mentioned"
        )
        embed = discord.Embed(title="setup", colour=discord.Colour.blurple())
        embed.add_field(name="channels", value=value, inline=False)
        embed.add_field(name="voice mode", value=prefs.voice_mode, inline=True)
        embed.add_field(name="voice frequency", value=f"{int(prefs.voice_frequency * 100)}%", inline=True)
        embed.add_field(name="images", value="on" if prefs.images_enabled else "off", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @setup_group.command(name="channel", description="Add or remove a channel Leyla listens in")
    @app_commands.describe(channel="Channel to configure", mode="add, remove, or clear")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="clear", value="clear"),
        ]
    )
    async def setup_channel(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Server-only command.", ephemeral=True)
            return
        runtime = self.services.runtime
        if mode.value == "clear":
            await runtime.clear_channels(interaction.guild.id)
            await interaction.response.send_message("Channel whitelist cleared.", ephemeral=True)
            return
        if channel is None:
            await interaction.response.send_message("Pick a channel.", ephemeral=True)
            return
        if mode.value == "add":
            channels = await runtime.add_channel(interaction.guild.id, channel.id)
        else:
            channels = await runtime.remove_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(
            "Listening channels: "
            + (", ".join(f"<#{cid}>" for cid in sorted(channels)) or "none"),
            ephemeral=True,
        )

    @setup_group.command(name="reset", description="Reset server channel settings")
    async def setup_reset(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Server-only command.", ephemeral=True)
            return
        await self.services.runtime.reset_all()
        await interaction.response.send_message("Channel settings reset.", ephemeral=True)

    # ------------------------------------------------------------------ voice
    @voice_group.command(name="mode", description="When Leyla should send voice notes")
    @app_commands.choices(
        mode=[app_commands.Choice(name=option, value=option) for option in VOICE_MODES]
    )
    async def voice_mode(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        prefs = await self.services.preferences.set_voice_mode(
            self.scope(interaction), self.scope(interaction), mode.value
        )
        descriptions = {
            "off": "voice notes disabled — text only",
            "request": "only when you ask for one",
            "occasional": f"sometimes, roughly {int(prefs.voice_frequency * 100)}% of suitable replies",
            "always": "every suitable reply",
        }
        await interaction.response.send_message(
            f"voice mode: **{prefs.voice_mode}** — {descriptions[prefs.voice_mode]}", ephemeral=True
        )

    @voice_group.command(name="frequency", description="How often Leyla adds a voice note (0-100%)")
    @app_commands.describe(percent="0 disables occasional voice notes, 100 is every suitable reply")
    async def voice_frequency(self, interaction: discord.Interaction, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        prefs = await self.services.preferences.set_voice_frequency(
            self.scope(interaction), self.scope(interaction), percent / 100
        )
        await interaction.response.send_message(
            f"voice frequency: **{int(prefs.voice_frequency * 100)}%**", ephemeral=True
        )

    @voice_group.command(name="status", description="Voice configuration and remaining budget")
    async def voice_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        s = self.services
        usage = await s.voice.usage_report()
        prefs = await s.preferences.get(self.scope(interaction), self.scope(interaction))
        embed = discord.Embed(title="voice", colour=discord.Colour.purple())
        embed.add_field(name="provider", value=s.settings.voice_provider, inline=True)
        embed.add_field(name="mode", value=prefs.voice_mode, inline=True)
        embed.add_field(name="frequency", value=f"{int(prefs.voice_frequency * 100)}%", inline=True)
        embed.add_field(
            name="budget",
            value=f"{usage['today']}/{usage['daily_limit']} chars today\n"
            f"{usage['month']}/{usage['monthly_limit']} this month",
            inline=False,
        )
        embed.add_field(name="audio pipeline", value="ffmpeg ✓" if s.voice.ffmpeg_available else "ffmpeg ✗", inline=True)
        embed.add_field(
            name="expressions",
            value="audio tags" if s.voice.provider.supports_audio_tags else "prosody only",
            inline=True,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @voice_group.command(name="test", description="Generate and send a real voice note now")
    @app_commands.describe(text="Optional text; leave empty for a default line")
    async def voice_test(self, interaction: discord.Interaction, text: str | None = None) -> None:
        await interaction.response.defer(thinking=True)
        s = self.services
        if not s.voice.enabled:
            await interaction.followup.send(
                "Voice is disabled — set VOICE_PROVIDER and the matching API key first.",
                ephemeral=True,
            )
            return
        line = (text or "hey. testing, testing — okay, I think this one works.").strip()[:400]
        mood = await s.mood.get(self.scope(interaction))
        channel = interaction.channel
        if channel is None:
            await interaction.followup.send("No channel available for the test.", ephemeral=True)
            return
        result = await s.voice.maybe_send(
            channel=channel,
            bot=self.bot,
            scope_id=self.scope(interaction),
            reply=line,
            mood=mood,
            request="explicit",
            mode="always",
            frequency=1.0,
        )
        if result.sent:
            await interaction.followup.send(
                f"sent — {result.duration:.1f}s, {result.characters} chars, {result.provider}/{result.model}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(f"couldn't send it: {result.reason}", ephemeral=True)

    # ----------------------------------------------------------------- memory
    @memory_group.command(name="list", description="List what Leyla remembers")
    @app_commands.describe(kind="Filter by kind", page="Page number (25 per page)")
    async def memory_list(
        self,
        interaction: discord.Interaction,
        kind: str | None = None,
        page: int = 1,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        page = max(1, page)
        memories = await self.services.memories.list(
            self.scope(interaction), limit=25, kind=kind, offset=(page - 1) * 25
        )
        total = await self.services.memories.count(self.scope(interaction))
        if not memories:
            await interaction.followup.send("Nothing stored yet.", ephemeral=True)
            return
        lines = [f"`#{m.id}` **{m.kind}** · {truncate(m.content, 140)}" for m in memories]
        embed = discord.Embed(
            title=f"memory ({total} total)",
            description="\n".join(lines)[:4000],
            colour=discord.Colour.teal(),
        )
        embed.set_footer(text=f"page {page} · /memory-delete <id> to forget one")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @memory_group.command(name="add", description="Teach Leyla something to remember")
    @app_commands.describe(text="A durable fact about you", kind="fact, preference, person, date, plan, boundary, topic")
    async def memory_add(
        self,
        interaction: discord.Interaction,
        text: str,
        kind: str = "fact",
        importance: int = 70,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        memory, created = await self.services.memories.add(
            self.scope(interaction),
            text,
            kind=kind.lower(),
            importance=max(0, min(100, importance)) / 100,
            subject="manual",
        )
        if memory is None:
            await interaction.followup.send("That was too short to store.", ephemeral=True)
            return
        verb = "saved" if created else "already knew that"
        await interaction.followup.send(f"{verb}: `#{memory.id}` {memory.content}", ephemeral=True)

    @memory_group.command(name="search", description="Search Leyla's memory")
    async def memory_search(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(ephemeral=True)
        results = await self.services.memories.search(self.scope(interaction), query, limit=8)
        if not results:
            await interaction.followup.send("Nothing relevant.", ephemeral=True)
            return
        lines = [f"`#{m.id}` [{m.kind}] {truncate(m.content, 160)} · score {s:.2f}" for m, s in results]
        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)

    @memory_group.command(name="reset", description="Clear the recent conversation window (keeps memories)")
    async def memory_reset(self, interaction: discord.Interaction) -> None:
        removed = await self.services.engine.reset_conversation(self.scope(interaction))
        await interaction.response.send_message(
            f"cleared {removed} recent messages. long-term memories are untouched.", ephemeral=True
        )

    @app_commands.command(name="memory-delete", description="Delete one memory by ID")
    @app_commands.describe(memory_id="The #ID shown by /memory list")
    async def memory_delete(self, interaction: discord.Interaction, memory_id: str) -> None:
        try:
            parsed = parse_memory_id(memory_id)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        deleted = await self.services.memories.delete(self.scope(interaction), parsed)
        await interaction.response.send_message(
            f"forgot `#{parsed}`." if deleted else f"no memory `#{parsed}`.", ephemeral=True
        )

    @app_commands.command(name="memory-clear", description="Delete every stored memory (needs confirm)")
    @app_commands.describe(confirm="Type 'yes' to confirm")
    async def memory_clear(self, interaction: discord.Interaction, confirm: str) -> None:
        if confirm.strip().lower() not in {"yes", "confirm", "delete"}:
            await interaction.response.send_message(
                "Not cleared. Run again with confirm: yes if you mean it.", ephemeral=True
            )
            return
        removed = await self.services.memories.clear(self.scope(interaction))
        await interaction.response.send_message(f"deleted {removed} memories.", ephemeral=True)

    # ------------------------------------------------------------------ photo
    @app_commands.command(name="photo", description="Send a picture of Leyla")
    @app_commands.describe(scene="Optional scene to generate (requires image generation)")
    async def photo(self, interaction: discord.Interaction, scene: str | None = None) -> None:
        await interaction.response.defer(thinking=True)
        s = self.services
        prefs = await s.preferences.get(self.scope(interaction), self.scope(interaction))
        if not prefs.images_enabled:
            await interaction.followup.send("Pictures are turned off.", ephemeral=True)
            return
        mood = await s.mood.get(self.scope(interaction))

        try:
            if scene:
                result = await s.images.generate(scene)
                await interaction.followup.send(
                    file=discord.File(result.path),
                    content=f"{mood.spec.emoji} {scene.strip()[:120]}",
                )
                return
        except ImageError as exc:
            await interaction.followup.send(f"couldn't make that picture: {exc}", ephemeral=True)
            return

        portrait = s.images.portrait_for(mood.mood)
        if portrait is None:
            await interaction.followup.send(
                "I don't have a portrait set up yet — add one to assets/character/.", ephemeral=True
            )
            return
        await interaction.followup.send(file=discord.File(portrait))


def _rss_mb() -> str:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB, macOS bytes.
        value = usage / 1024 if usage < 10**8 else usage / (1024 * 1024)
        return f"{value:.0f} MB"
    except Exception:  # noqa: BLE001 - platform dependent
        return "n/a"


__all__ = ["AdminCog", "VoiceError"]
