import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot.command_sync import (
    format_command_names,
    registered_command_names,
    sync_registered_commands,
)
from bot.commands import register_commands
from bot.config import settings
from bot.services.club_night_scheduler import (
    run_club_night_announcement_tick,
    run_club_night_reminder_tick,
)
from bot.services.game_organisation_export import run_game_organisation_export_tick
from bot.services.state_sync import run_state_sync_tick


_CLOUD_SEVERITY: dict[int, str] = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class _StructuredFormatter(logging.Formatter):
    """JSON formatter compatible with Cloud Logging structured log ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            message = message + "\n" + record.exc_text
        return json.dumps({
            "severity": _CLOUD_SEVERITY.get(record.levelno, "DEFAULT"),
            "message": message,
            "logger": record.name,
        })


_handler = logging.StreamHandler()
_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.stderr.isatty()
    else _StructuredFormatter()
)
logging.basicConfig(level=logging.INFO, handlers=[_handler])


def _check_forum_tag(channel: discord.ForumChannel, tag_id: int | None) -> None:
    """Log whether the configured forum tag exists on the channel.

    Logs ERROR if the tag ID is set but not found among the channel's available
    tags, so misconfiguration is surfaced at startup rather than mid-command.
    """
    if tag_id is None:
        logging.info("CLUB_NIGHT_FORUM_TAG_ID not set; posts will be created without a tag.")
        return

    valid_ids = {tag.id for tag in channel.available_tags}

    if tag_id not in valid_ids:
        logging.error(
            "CLUB_NIGHT_FORUM_TAG_ID %s is not a valid tag on forum channel %r (%s). "
            "Valid tag IDs: %s. /post-club-night will fail if a tag is required.",
            tag_id,
            channel.name,
            channel.id,
            sorted(valid_ids),
        )
    else:
        logging.info(
            "Forum tag ID %s verified on channel %r (%s).",
            tag_id,
            channel.name,
            channel.id,
        )


def _log_startup_health() -> None:
    guild_target = str(settings.discord_guild_id) if settings.discord_guild_id is not None else "global"
    logging.info("Guild target: %s", guild_target)

    summaries_path = Path(settings.summaries_dir)
    md_count = sum(1 for _ in summaries_path.rglob("*.md"))
    logging.info("Summaries: %s — %d .md file(s) found", settings.summaries_dir, md_count)

    forum_configured = bool(os.getenv("CLUB_NIGHT_FORUM_CHANNEL_ID"))
    logging.info(
        "Club night forum channel: %s (%s)",
        settings.club_night_forum_channel_id,
        "configured" if forum_configured else "default",
    )

    logging.info(
        "Announcement template: %s",
        settings.club_night_announcement_template_path,
    )

    logging.info(
        "Reminder template: %s",
        settings.club_night_reminder_template_path,
    )

    stadium_url_configured = bool(os.getenv("CARDIFF_STADIUM_EVENTS_URL"))
    logging.info(
        "Cardiff Stadium events URL: %s (%s)",
        settings.cardiff_stadium_events_url,
        "configured" if stadium_url_configured else "default",
    )

    battlefields_url_configured = bool(os.getenv("BATTLEFIELDS_EVENTS_URL"))
    logging.info(
        "Battlefields events URL: %s (%s)",
        settings.battlefields_events_url,
        "configured" if battlefields_url_configured else "default",
    )

    logging.info(
        "Game organisation export: %s (channel=%s path=%s)",
        "enabled" if settings.game_organisation_export_enabled else "disabled",
        settings.game_organisation_channel_id,
        settings.game_organisation_export_path,
    )

    logging.info(
        "GCS state upload: %s (bucket=%s)",
        "enabled" if settings.gcs_state_upload_enabled else "disabled",
        settings.gcs_state_bucket,
    )


class BFWGBot(commands.Bot):
    def __init__(self, *, sync_commands_on_startup: bool | None = None) -> None:
        intents = discord.Intents.default()
        # Privileged intent — must also be enabled on the bot's Discord
        # Developer Portal page, or the gateway refuses the connection.
        # Without it, message.content is empty in the game organisation
        # export and /faction-distribution reports everyone as Unknown.
        intents.message_content = True
        super().__init__(
            command_prefix=[],
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self._club_night_scheduler_task: asyncio.Task[None] | None = None
        self.sync_commands_on_startup = (
            settings.sync_commands_on_startup
            if sync_commands_on_startup is None
            else sync_commands_on_startup
        )

    async def setup_hook(self) -> None:
        register_commands(self.tree)
        logging.info(
            "Registered app command tree: %s",
            format_command_names(registered_command_names(self.tree)),
        )
        _log_startup_health()

        if not self.sync_commands_on_startup:
            logging.info(
                "Skipping Discord command sync on startup. "
                "Use /admin sync-commands or run `python -m bot sync-commands` "
                "when the Discord command tree needs updating."
            )
            return

        result = await sync_registered_commands(self.tree, settings.discord_guild_id)
        logging.info(
            "Synced %s %s command(s): %s",
            len(result.command_names),
            result.scope,
            format_command_names(result.command_names),
        )

    async def on_ready(self) -> None:
        if self.user is None:
            logging.info("Bot is ready.")
            return

        logging.info("Logged in as %s (%s).", self.user, self.user.id)
        await self._validate_forum_channel()
        await self._validate_command_docs_channel()
        await self._validate_game_organisation_channel()
        self._ensure_club_night_scheduler()

    def _ensure_club_night_scheduler(self) -> None:
        if not (
            settings.club_night_reminder_scheduler_enabled
            or settings.club_night_announcement_scheduler_enabled
            or settings.game_organisation_export_enabled
            or settings.gcs_state_upload_enabled
        ):
            return

        if (
            self._club_night_scheduler_task is not None
            and not self._club_night_scheduler_task.done()
        ):
            return

        self._club_night_scheduler_task = asyncio.create_task(
            self._run_club_night_scheduler(),
            name="club-night-scheduler",
        )
        logging.info(
            "Started club night scheduler interval=%ss reminder=%s announcement=%s "
            "game_organisation_export=%s state_sync=%s.",
            settings.club_night_reminder_check_interval_seconds,
            settings.club_night_reminder_scheduler_enabled,
            settings.club_night_announcement_scheduler_enabled,
            settings.game_organisation_export_enabled,
            settings.gcs_state_upload_enabled,
        )

    async def _run_club_night_scheduler(self) -> None:
        await self.wait_until_ready()

        while not self.is_closed():
            if settings.club_night_announcement_scheduler_enabled:
                await self._run_scheduler_tick(
                    "announcement", run_club_night_announcement_tick
                )

            if settings.club_night_reminder_scheduler_enabled:
                await self._run_scheduler_tick("reminder", run_club_night_reminder_tick)

            if settings.game_organisation_export_enabled:
                await self._run_scheduler_tick(
                    "game organisation export", run_game_organisation_export_tick
                )

            # Last on purpose: a fresh export written by the tick above is
            # uploaded in the same pass.
            if settings.gcs_state_upload_enabled:
                await self._run_scheduler_tick("state sync", run_state_sync_tick)

            await asyncio.sleep(settings.club_night_reminder_check_interval_seconds)

    async def _run_scheduler_tick(self, tick_name, tick) -> None:
        try:
            result = await tick(self, settings)
            logging.info(
                "Scheduler %s tick status=%s reason=%s post_id=%s.",
                tick_name,
                result.status,
                result.reason,
                result.post_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Scheduler %s tick failed unexpectedly.", tick_name)

    async def _stop_club_night_scheduler(self) -> None:
        task = self._club_night_scheduler_task

        if task is None:
            return

        self._club_night_scheduler_task = None

        if task.done():
            return

        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            logging.info("Stopped club night scheduler.")

    async def _validate_forum_channel(self) -> None:
        channel_id = settings.club_night_forum_channel_id
        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)

            if not isinstance(channel, discord.ForumChannel):
                logging.error(
                    "CLUB_NIGHT_FORUM_CHANNEL_ID %s resolves to %s, not a ForumChannel. "
                    "/post-club-night will fail until this is corrected.",
                    channel_id,
                    type(channel).__name__,
                )
            else:
                logging.info(
                    "Forum channel %r (%s) verified accessible.",
                    channel.name,
                    channel_id,
                )
                _check_forum_tag(channel, settings.club_night_forum_tag_id)
        except discord.NotFound:
            logging.error(
                "CLUB_NIGHT_FORUM_CHANNEL_ID %s was not found. "
                "/post-club-night will fail until this is corrected.",
                channel_id,
            )
        except discord.Forbidden:
            logging.error(
                "Missing access to CLUB_NIGHT_FORUM_CHANNEL_ID %s. "
                "/post-club-night will fail until this is corrected.",
                channel_id,
            )
        except discord.HTTPException as exc:
            logging.error(
                "Could not verify CLUB_NIGHT_FORUM_CHANNEL_ID %s: %s. "
                "/post-club-night may fail.",
                channel_id,
                exc,
            )

    async def _validate_command_docs_channel(self) -> None:
        channel_id = settings.command_docs_channel_id
        if channel_id is None:
            logging.info(
                "COMMAND_DOCS_CHANNEL_ID not set; /admin sync-commands will not publish a docs post."
            )
            return

        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)

            if not (hasattr(channel, "send") and hasattr(channel, "history")):
                logging.error(
                    "COMMAND_DOCS_CHANNEL_ID %s resolves to %s, which cannot host a text post. "
                    "/admin sync-commands will not update the command docs post.",
                    channel_id,
                    type(channel).__name__,
                )
            else:
                logging.info(
                    "Command docs channel %r (%s) verified accessible.",
                    getattr(channel, "name", "unknown"),
                    channel_id,
                )
        except discord.NotFound:
            logging.error(
                "COMMAND_DOCS_CHANNEL_ID %s was not found. "
                "/admin sync-commands will not update the command docs post.",
                channel_id,
            )
        except discord.Forbidden:
            logging.error(
                "Missing access to COMMAND_DOCS_CHANNEL_ID %s. "
                "/admin sync-commands will not update the command docs post.",
                channel_id,
            )
        except discord.HTTPException as exc:
            logging.error(
                "Could not verify COMMAND_DOCS_CHANNEL_ID %s: %s. "
                "/admin sync-commands may fail to update the command docs post.",
                channel_id,
                exc,
            )

    async def _validate_game_organisation_channel(self) -> None:
        channel_id = settings.game_organisation_channel_id
        if channel_id is None:
            logging.info(
                "GAME_ORGANISATION_CHANNEL_ID not set; the game organisation export is off."
            )
            return

        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)

            if not isinstance(channel, discord.ForumChannel):
                logging.error(
                    "GAME_ORGANISATION_CHANNEL_ID %s resolves to %s, not a ForumChannel. "
                    "The game organisation export will fail until this is corrected.",
                    channel_id,
                    type(channel).__name__,
                )
            else:
                logging.info(
                    "Game organisation channel %r (%s) verified accessible.",
                    channel.name,
                    channel_id,
                )
        except discord.NotFound:
            logging.error(
                "GAME_ORGANISATION_CHANNEL_ID %s was not found. "
                "The game organisation export will fail until this is corrected.",
                channel_id,
            )
        except discord.Forbidden:
            logging.error(
                "Missing access to GAME_ORGANISATION_CHANNEL_ID %s. "
                "The game organisation export will fail until this is corrected.",
                channel_id,
            )
        except discord.HTTPException as exc:
            logging.error(
                "Could not verify GAME_ORGANISATION_CHANNEL_ID %s: %s. "
                "The game organisation export may fail.",
                channel_id,
                exc,
            )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        command_name = interaction.command.name if interaction.command else "unknown"
        guild_id = interaction.guild_id
        channel_id = interaction.channel_id
        user_id = getattr(interaction.user, "id", None)

        if isinstance(error, app_commands.CommandInvokeError) and isinstance(
            error.original, discord.NotFound
        ):
            logging.warning(
                "/%s interaction expired before the bot could respond "
                "guild=%s channel=%s user=%s. "
                "Check that only one worker instance is running this Discord bot token.",
                command_name,
                guild_id,
                channel_id,
                user_id,
            )
            return

        logging.error(
            "Unhandled app command error command=%s guild=%s channel=%s user=%s",
            command_name,
            guild_id,
            channel_id,
            user_id,
            exc_info=(type(error), error, error.__traceback__),
        )

    async def close(self) -> None:
        await self._stop_club_night_scheduler()
        await super().close()


async def main() -> None:
    bot = BFWGBot()

    async with bot:
        await bot.start(settings.discord_token)


async def sync_commands_once() -> None:
    bot = BFWGBot(sync_commands_on_startup=False)

    async with bot:
        await bot.login(settings.discord_token)
        result = await sync_registered_commands(bot.tree, settings.discord_guild_id)
        logging.info(
            "Synced %s %s command(s): %s",
            len(result.command_names),
            result.scope,
            format_command_names(result.command_names),
        )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sync-commands":
        asyncio.run(sync_commands_once())
    else:
        asyncio.run(main())
