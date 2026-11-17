"""Bot startup, command registration, routing and error handling."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from discord import app_commands

from leyla.bot import LeylaBot
from leyla.errors import UnauthorizedError


@pytest.mark.asyncio
async def test_setup_hook_registers_all_commands(settings):
    bot = LeylaBot(settings, sync_commands=False)
    await bot.setup_hook()
    try:
        names = {command.name for command in bot.tree.get_commands()}
        expected = {
            "help",
            "setup",
            "chat",
            "voice",
            "mood",
            "memory",
            "memory-delete",
            "memory-clear",
            "status",
            "photo",
        }
        assert expected <= names
        assert bot.services is not None
        assert bot.services.db is not None
        # Voice is off in the fixture, so the null provider must be selected.
        assert bot.services.voice.enabled is False
    finally:
        await bot.shutdown()


@pytest.mark.asyncio
async def test_non_owner_messages_are_ignored(settings):
    bot = LeylaBot(settings, sync_commands=False)
    bot._remember = lambda message_id: pytest.fail("non-owner message must not be processed")

    message = SimpleNamespace(
        author=SimpleNamespace(id=999, bot=False),
        content="let me in",
        guild=None,
        id=1,
        mentions=[],
    )
    await bot.on_message(message)  # must return silently


@pytest.mark.asyncio
async def test_bot_own_messages_ignored(settings):
    bot = LeylaBot(settings, sync_commands=False)
    bot._remember = lambda message_id: pytest.fail("own message must not be processed")
    message = SimpleNamespace(
        author=SimpleNamespace(id=42, bot=True),
        content="talking to myself",
        guild=None,
        id=2,
        mentions=[],
    )
    await bot.on_message(message)


def test_duplicate_message_ids_are_dropped(settings):
    bot = LeylaBot(settings, sync_commands=False)
    assert bot._remember(10) is True
    assert bot._remember(10) is False
    assert bot._remember(11) is True


@pytest.mark.asyncio
async def test_channel_routing_rules(settings):
    bot = LeylaBot(settings, sync_commands=False)
    await bot.setup_hook()
    try:
        dm = SimpleNamespace(guild=None)
        assert await bot._channel_allowed(dm) is True

        bot._connection.user = SimpleNamespace(id=1)
        guild = SimpleNamespace(id=555)
        unmentioned = SimpleNamespace(guild=guild, channel=SimpleNamespace(id=10), mentions=[])
        assert await bot._channel_allowed(unmentioned) is False

        mentioned = SimpleNamespace(guild=guild, channel=SimpleNamespace(id=10), mentions=[bot.user])
        assert await bot._channel_allowed(mentioned) is True

        await bot.services.runtime.add_channel(555, 10)
        assert await bot._channel_allowed(unmentioned) is True
        assert await bot._channel_allowed(
            SimpleNamespace(guild=guild, channel=SimpleNamespace(id=11), mentions=[])
        ) is False
    finally:
        await bot.shutdown()


@pytest.mark.asyncio
async def test_error_handler_hides_internal_details(settings):
    bot = LeylaBot(settings, sync_commands=False)
    sent: dict = {}

    class FakeResponse:
        def is_done(self) -> bool:
            return False

        async def send_message(self, message, ephemeral=False):
            sent.update({"message": message, "ephemeral": ephemeral})

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=999),
        response=FakeResponse(),
        followup=None,
    )
    await bot.on_app_command_error(interaction, UnauthorizedError("secret internals"))

    assert sent["ephemeral"] is True
    assert "secret internals" not in sent["message"]
    assert sent["message"] == "this one is private."


@pytest.mark.asyncio
async def test_error_handler_uses_followup_after_defer(settings):
    bot = LeylaBot(settings, sync_commands=False)
    sent: dict = {}

    class FakeResponse:
        def is_done(self) -> bool:
            return True

    class FakeFollowup:
        async def send(self, message, ephemeral=False):
            sent.update({"message": message, "ephemeral": ephemeral})

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42), response=FakeResponse(), followup=FakeFollowup()
    )
    await bot.on_app_command_error(interaction, app_commands.AppCommandError("boom"))
    assert sent["message"]
