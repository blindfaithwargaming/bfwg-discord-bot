import logging

import discord
from discord import app_commands

from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import (
    check_rate_limit,
    command_context,
    defer_ephemeral,
)
from bot.config import settings
from bot.services import state_sync
from bot.services.discord_text import escape_public_text
from bot.services.feedback import FEEDBACK_COMMAND_CHOICES, FeedbackStore, clean_comment
from bot.services.ids import validate_guild_id
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT, truncate


GUILD_ONLY_MESSAGE = "This command only works in a server."
STORE_ERROR_MESSAGE = "Could not save your feedback; try again or tell an organiser."

_COMMAND_CHOICES = [
    app_commands.Choice(name=name, value=value)
    for name, value in FEEDBACK_COMMAND_CHOICES
]


def _feedback_store() -> FeedbackStore:
    return FeedbackStore(settings.feedback_state_path)


def register_feedback_commands(tree: discord.app_commands.CommandTree) -> None:
    feedback_group = app_commands.Group(
        name="feedback",
        description="Report a bug or suggest an improvement to the bot.",
    )

    @feedback_group.command(
        name="submit",
        description="Submit a bug report or suggestion about the bot.",
    )
    @app_commands.describe(
        command="Which command is this about? Choose Other for anything else.",
        comment="Your bug report or suggestion.",
    )
    @app_commands.choices(command=_COMMAND_CHOICES)
    async def submit(
        interaction: discord.Interaction,
        command: app_commands.Choice[str],
        comment: str,
    ) -> None:
        async with command_context(interaction, "feedback submit"):
            if not await check_rate_limit(interaction, "feedback submit"):
                return
            if not await defer_ephemeral(interaction, "feedback submit"):
                return

            if interaction.guild_id is None:
                await interaction.followup.send(
                    GUILD_ONLY_MESSAGE,
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            guild_id = validate_guild_id(str(interaction.guild_id))

            try:
                cleaned_comment = clean_comment(comment)
            except ValueError as exc:
                await interaction.followup.send(
                    str(exc),
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            user = interaction.user
            display_name = str(getattr(user, "display_name", None) or user)

            try:
                _feedback_store().add_feedback(
                    guild_id,
                    str(user.id),
                    display_name,
                    command.value,
                    cleaned_comment,
                )
            except OSError:
                logging.exception("Could not persist feedback submission.")
                await interaction.followup.send(
                    STORE_ERROR_MESSAGE,
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            state_sync.schedule_upload(settings.feedback_state_path, settings)

            confirmation = truncate(
                "Thanks — your feedback has been logged for the organisers.\n"
                f"> {escape_public_text(cleaned_comment)}",
                DISCORD_MESSAGE_LIMIT,
            )
            await interaction.followup.send(
                confirmation,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )

    tree.add_command(feedback_group)
