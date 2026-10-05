import logging

import discord
from discord import app_commands

from bot.commands.club_night_group import _club_night_group
from bot.commands.club_night_pairings import _resolve_club_night
from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import (
    check_rate_limit,
    command_context,
    defer_ephemeral,
    require_publish_permission,
)
from bot.config import settings
from bot.services.club_night_announcement import ReminderTemplateError
from bot.services.club_night_reminder import build_club_night_reminder_text
from bot.services.stadium_events import format_date


@_club_night_group.command(
    name="remind",
    description="Post the Heresy reminder in this event thread.",
)
@app_commands.describe(
    days_before="Only post if the club night is within this many days (default 3).",
)
async def club_night_remind(
    interaction: discord.Interaction,
    days_before: app_commands.Range[int, 0, 30] = 3,
) -> None:
    async with command_context(interaction, "club-night remind"):
        if not await require_publish_permission(interaction):
            return
        if not await check_rate_limit(interaction, "club-night remind"):
            return
        if not await defer_ephemeral(interaction, "club-night remind"):
            return

        try:
            club_night = await _resolve_club_night(interaction)
            if club_night is None:
                return
            if club_night.game != "heresy":
                await interaction.followup.send(
                    "Reminder templates are currently only available for Heresy. "
                    "Use `/club-night signup-info` to post this event's signup procedure.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            reminder = build_club_night_reminder_text(
                club_night,
                template_path=settings.club_night_reminder_template_path,
            )
        except ReminderTemplateError as exc:
            logging.exception("Could not prepare the club night reminder.")
            await interaction.followup.send(
                str(exc), ephemeral=True, allowed_mentions=NO_MENTIONS
            )
            return

        if reminder.days_until > days_before:
            day_word = "day" if reminder.days_until == 1 else "days"
            await interaction.followup.send(
                f"The next club night is {reminder.days_until} {day_word} away "
                f"({format_date(reminder.club_night.event_date)}). "
                f"Set `days_before` to {reminder.days_until} or higher to post a reminder now.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        channel = interaction.channel

        if channel is None:
            await interaction.followup.send(
                "Could not determine the current channel.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        await channel.send(reminder.text, allowed_mentions=NO_MENTIONS)
        await interaction.followup.send(
            "Reminder posted.", ephemeral=True, allowed_mentions=NO_MENTIONS
        )
        logging.info(
            "Posted club night reminder channel=%s date=%s days_until=%d.",
            interaction.channel_id,
            format_date(reminder.club_night.event_date),
            reminder.days_until,
        )
