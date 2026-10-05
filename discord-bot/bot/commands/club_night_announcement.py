import asyncio
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
    require_admin_or_owner,
    require_publish_permission,
)
from bot.config import settings
from bot.services.discord_text import escape_public_text
from bot.services.organised_play import build_signup_instructions
from bot.services.club_night_announcement import (
    AnnouncementTemplateError,
    ClubNightAnnouncementError,
    build_announcement_content,
    find_duplicate_announcement_thread,
    get_applied_tags,
    get_stadium_status_lines,
    is_duplicate_announcement_title,
)
from bot.services.stadium_events import (
    ClubNight,
    StadiumEventsError,
    format_date,
)


_active_post_task: asyncio.Task[object] | None = None
_active_post_task_lock = asyncio.Lock()
RECENT_FORUM_THREAD_LIMIT = 50


def register_club_night_announcement_command(tree: discord.app_commands.CommandTree) -> None:
    @tree.command(
        name="post-club-night",
        description="Create a forum post for the next Blind Faith organised play night.",
    )
    @app_commands.describe(refresh="Fetch fresh club-night data instead of using the cache.")
    @app_commands.default_permissions(administrator=True)
    async def post_club_night(interaction: discord.Interaction, refresh: bool = False) -> None:
        async with command_context(interaction, "post-club-night"):
            if not await require_publish_permission(interaction):
                return
            if not await defer_ephemeral(interaction, "post-club-night"):
                return

            current_task = asyncio.current_task()

        if current_task is not None:
            await _replace_active_post_task(current_task)

        try:
            club_night, title, body = await build_announcement_content(settings, refresh=refresh)
            forum_channel = await _get_forum_channel(interaction)
            existing_thread = await _find_existing_forum_post(forum_channel, title)

            if existing_thread is not None:
                logging.info(
                    "Skipped duplicate club night forum post for %s; existing thread is %s.",
                    format_date(club_night.event_date),
                    existing_thread.id,
                )
                await interaction.followup.send(
                    f"A forum post for {format_date(club_night.event_date)} already exists: "
                    f"{existing_thread.mention}\n"
                    "I did not create a duplicate.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            applied_tags = get_applied_tags(forum_channel, settings.club_night_forum_tag_id)
            reason = f"Blind Faith organised play announcement requested by {interaction.user}"

            if applied_tags is None:
                created = await forum_channel.create_thread(
                    name=title,
                    content=body,
                    allowed_mentions=NO_MENTIONS,
                    reason=reason,
                )
            else:
                created = await forum_channel.create_thread(
                    name=title,
                    content=body,
                    allowed_mentions=NO_MENTIONS,
                    applied_tags=applied_tags,
                    reason=reason,
                )
            thread = _extract_thread(created)

            logging.info(
                "Created club night forum post %s for %s.",
                thread.id,
                format_date(club_night.event_date),
            )
            await interaction.followup.send(
                f"Created forum post for {format_date(club_night.event_date)}: {thread.mention}",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
        except (AnnouncementTemplateError, StadiumEventsError, ClubNightAnnouncementError) as exc:
            logging.exception("Could not create club night announcement.")
            await interaction.followup.send(
                str(exc), ephemeral=True, allowed_mentions=NO_MENTIONS
            )
            return
        except discord.Forbidden:
            logging.exception("Missing Discord permissions to create a club night forum post.")
            await interaction.followup.send(
                "I do not have permission to create a post in the configured forum channel.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        except discord.HTTPException as exc:
            logging.exception("Discord rejected the club night forum post.")
            await interaction.followup.send(
                _format_discord_post_error(exc),
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        except asyncio.CancelledError:
            logging.info("Cancelled superseded /post-club-night request.")
            return
        finally:
            if current_task is not None:
                await _clear_active_post_task(current_task)


@_club_night_group.command(
    name="next",
    description="Show this event and, for Heresy, any stadium conflicts.",
)
@app_commands.describe(
    refresh="Fetch fresh club-night data instead of using the cache."
)
async def club_night_next(interaction: discord.Interaction, refresh: bool = False) -> None:
    async with command_context(interaction, "club-night next"):
        if not await require_admin_or_owner(interaction, "club-night next"):
            return
        if not await check_rate_limit(interaction, "club-night next"):
            return
        if not await defer_ephemeral(interaction, "club-night next"):
            return

        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return
        if club_night.game != "heresy":
            await interaction.followup.send(
                f"{escape_public_text(club_night.title)} — {format_date(club_night.event_date)}",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        stadium_status_lines = await get_stadium_status_lines(
            settings,
            club_night.event_date,
            refresh=refresh,
        )
        await interaction.followup.send(
            _format_next_club_night_reply(club_night, stadium_status_lines),
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )


@_club_night_group.command(
    name="preview",
    description="Preview the signup procedure for this event without publishing it.",
)
@app_commands.describe(
    refresh="Retained for compatibility; this preview uses the current thread."
)
async def club_night_preview(interaction: discord.Interaction, refresh: bool = False) -> None:
    async with command_context(interaction, "club-night preview"):
        if not await require_admin_or_owner(interaction, "club-night preview"):
            return
        if not await check_rate_limit(interaction, "club-night preview"):
            return
        if not await defer_ephemeral(interaction, "club-night preview"):
            return

        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return
        await interaction.followup.send(
            build_signup_instructions(event=club_night),
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )


def register_club_night_commands(tree: discord.app_commands.CommandTree) -> None:
    tree.add_command(_club_night_group)


async def _replace_active_post_task(current_task: asyncio.Task[object]) -> None:
    global _active_post_task

    previous_task: asyncio.Task[object] | None = None

    async with _active_post_task_lock:
        if (
            _active_post_task is not None
            and _active_post_task is not current_task
            and not _active_post_task.done()
        ):
            previous_task = _active_post_task
            previous_task.cancel()

        _active_post_task = current_task

    if previous_task is not None:
        logging.info("Cancelled previous /post-club-night request in favour of the newest request.")


async def _clear_active_post_task(current_task: asyncio.Task[object]) -> None:
    global _active_post_task

    async with _active_post_task_lock:
        if _active_post_task is current_task:
            _active_post_task = None


async def _get_forum_channel(interaction: discord.Interaction) -> discord.ForumChannel:
    channel = interaction.client.get_channel(settings.club_night_forum_channel_id)

    if channel is None:
        channel = await interaction.client.fetch_channel(settings.club_night_forum_channel_id)

    if not isinstance(channel, discord.ForumChannel):
        raise ClubNightAnnouncementError(
            f"Configured channel {settings.club_night_forum_channel_id} is not a forum channel."
        )

    return channel


async def _find_existing_forum_post(
    forum_channel: discord.ForumChannel,
    title: str,
) -> discord.Thread | None:
    try:
        active_threads = await forum_channel.guild.active_threads()
        active_match = find_duplicate_announcement_thread(
            (
                thread
                for thread in active_threads
                if getattr(thread, "parent_id", None) == forum_channel.id
            ),
            title,
        )

        if active_match is not None:
            return active_match

        async for thread in forum_channel.archived_threads(limit=RECENT_FORUM_THREAD_LIMIT):
            if is_duplicate_announcement_title(thread.name, title):
                return thread
    except discord.Forbidden as exc:
        raise ClubNightAnnouncementError(
            f"I could not check recent posts in {forum_channel.mention} for duplicates. "
            "Please check the bot can view forum threads."
        ) from exc
    except discord.HTTPException as exc:
        raise ClubNightAnnouncementError(
            f"I could not check recent posts in {forum_channel.mention} for duplicates: {exc}"
        ) from exc

    return None


def _extract_thread(created: object) -> discord.Thread:
    if isinstance(created, discord.Thread):
        return created

    thread = getattr(created, "thread", None)

    if isinstance(thread, discord.Thread):
        return thread

    if isinstance(created, tuple) and created and isinstance(created[0], discord.Thread):
        return created[0]

    raise ClubNightAnnouncementError("Discord created the forum post, but the thread response was not recognised.")


def _format_next_club_night_reply(
    club_night: ClubNight,
    stadium_status_lines: list[str],
) -> str:
    status = "\n".join(stadium_status_lines)
    return (
        f"Event: {escape_public_text(club_night.title)}\n"
        f"Date: {format_date(club_night.event_date)}\n\n"
        f"{status}\n\n"
        "Use /club-night preview to check the signup procedure, then /club-night signup-info to post it."
    )


def _format_discord_post_error(error: discord.HTTPException) -> str:
    message = str(error)

    if "tag" in message.lower():
        return (
            f"Discord rejected the forum post, possibly because the forum requires a tag. "
            f"Set CLUB_NIGHT_FORUM_TAG_ID to a valid tag ID for channel {settings.club_night_forum_channel_id}."
        )

    return f"Discord rejected the forum post: {error}"
