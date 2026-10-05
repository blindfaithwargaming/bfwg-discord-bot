import logging
from datetime import date as current_date
from typing import Optional

import discord
from discord import app_commands

from bot.commands.interaction_helpers import command_context, defer_ephemeral
from bot.config import settings
from bot.services.stadium_events import (
    StadiumEventsError,
    events_on_date,
    format_date,
    get_events,
    get_next_club_night,
    parse_date,
)


def register_stadium_event_command(tree: discord.app_commands.CommandTree) -> None:
    @tree.command(
        name="stadium-event",
        description="Check whether Cardiff City Stadium has a listed event on a date.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        date="Optional date to check, for example 2026-07-04 or 4 July 2026.",
        refresh="Fetch fresh event data instead of using the cache.",
    )
    async def stadium_event(
        interaction: discord.Interaction,
        date: Optional[str] = None,
        refresh: bool = False,
    ) -> None:
        async with command_context(interaction, "stadium-event"):
            if not await defer_ephemeral(interaction, "stadium-event"):
                return

            if date is None:
                try:
                    club_night = await get_next_club_night(
                        settings.battlefields_events_url,
                        settings.cardiff_stadium_events_timeout_seconds,
                        settings.cardiff_stadium_events_cache_seconds,
                        settings.cardiff_stadium_events_max_bytes,
                        force_refresh=refresh,
                    )
                except StadiumEventsError as exc:
                    logging.exception("Could not check the next Blind Faith club night.")
                    await interaction.followup.send(
                        f"I could not find the next Blind Faith club night: {exc}\n\n"
                        f"You can check events directly at {settings.battlefields_events_url}",
                        ephemeral=True,
                    )
                    return

                target_date = club_night.event_date
                logging.info(
                    "No date supplied for /stadium-event; using next Blind Faith club night: %s (%s).",
                    format_date(club_night.event_date),
                    club_night.title,
                )
            else:
                try:
                    target_date = parse_date(date)
                except ValueError as exc:
                    await interaction.followup.send(str(exc), ephemeral=True)
                    return

            today = current_date.today()

            if target_date < today:
                await interaction.followup.send(
                    "Please provide today or a future date. I cannot check past stadium listings reliably.",
                    ephemeral=True,
                )
                return

            try:
                snapshot = await get_events(
                    settings.cardiff_stadium_events_url,
                    settings.cardiff_stadium_events_timeout_seconds,
                    settings.cardiff_stadium_events_cache_seconds,
                    settings.cardiff_stadium_events_max_bytes,
                    force_refresh=refresh,
                )
            except StadiumEventsError as exc:
                logging.exception("Could not check Cardiff City Stadium events.")
                await interaction.followup.send(
                    f"I could not check the stadium events page: {exc}\n\n"
                    f"You can check events directly at {settings.cardiff_stadium_events_url}",
                    ephemeral=True,
                )
                return

            matches = events_on_date(snapshot.events, target_date)
            formatted_date = format_date(target_date)
            cache_note = ""

            if snapshot.from_cache:
                fetched_at = snapshot.fetched_at.strftime("%Y-%m-%d %H:%M UTC")
                cache_note = f"\n\nUsing cached event data fetched at {fetched_at}."

            if matches:
                event_lines = "\n".join(
                    f"- {event.title} at {event.starts_at.strftime('%H:%M')}" for event in matches
                )
                await interaction.followup.send(
                    f"Yes. Cardiff City Stadium has listed event(s) on {formatted_date}:\n"
                    f"{event_lines}\n\n"
                    f"Source: {settings.cardiff_stadium_events_url}"
                    f"{cache_note}",
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                f"I could not find a listed Cardiff City Stadium event on {formatted_date}.\n\n"
                f"Source checked: {settings.cardiff_stadium_events_url}"
                f"{cache_note}",
                ephemeral=True,
            )
