import logging
import os
from pathlib import Path

import discord
from discord import app_commands

from bot.command_sync import format_command_names, registered_command_names, sync_registered_commands
from bot.commands.interaction_helpers import command_context, defer_ephemeral
from bot.config import settings
from bot.services.command_docs import publish_command_docs
from bot.services.stadium_events import cache_age_seconds


def _build_diagnostics_text(
    *,
    bot_user: str,
    bot_user_id: int | None,
    summaries_dir: str,
    md_count: int,
    command_names: list[str],
    forum_channel_id: int,
    owner_count: int,
    cache_age: float | None,
    git_commit: str,
) -> str:
    user_line = f"**Bot:** {bot_user}"
    if bot_user_id is not None:
        user_line += f" (ID: {bot_user_id})"
    cache_line = (
        f"**Stadium cache:** {cache_age:.0f}s ago"
        if cache_age is not None
        else "**Stadium cache:** not populated"
    )
    lines = [
        user_line,
        f"**Commit:** {git_commit}",
        f"**Summaries:** {summaries_dir} — {md_count} .md file(s)",
        f"**Commands:** {len(command_names)} registered — {format_command_names(command_names)}",
        f"**Forum channel:** {forum_channel_id}",
        f"**Bot owner IDs:** {owner_count} configured",
        cache_line,
    ]
    return "\n".join(lines)


def _build_config_check_text(
    *,
    summaries_ok: bool,
    template_ok: bool,
    forum_channel_ok: bool,
    forum_tag_ok: bool | None,
) -> str:
    def mark(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    lines = [
        f"**Summaries dir:** {mark(summaries_ok)}",
        f"**Announcement template:** {mark(template_ok)}",
        f"**Forum channel:** {mark(forum_channel_ok)}",
    ]
    if forum_tag_ok is None:
        lines.append("**Forum tag:** not configured")
    else:
        lines.append(f"**Forum tag:** {mark(forum_tag_ok)}")
    return "\n".join(lines)


def register_admin_commands(tree: discord.app_commands.CommandTree) -> None:
    admin_group = app_commands.Group(
        name="admin",
        description="Admin-only bot operations.",
        default_permissions=discord.Permissions(administrator=True),
    )

    @admin_group.command(
        name="sync-commands",
        description="Sync the current slash command tree to Discord.",
    )
    async def sync_commands(interaction: discord.Interaction) -> None:
        async with command_context(interaction, "admin sync-commands"):
            if not await _require_admin_or_owner(interaction):
                return

            if not await defer_ephemeral(interaction, "admin sync-commands"):
                return

            result = await sync_registered_commands(
                interaction.client.tree,
                settings.discord_guild_id,
            )
            scope = "global commands"

            if result.guild_id is not None:
                scope = f"guild commands for {result.guild_id}"

            logging.info(
                "Admin command synced %s %s: %s",
                len(result.command_names),
                scope,
                format_command_names(result.command_names),
            )

            docs_note = await _update_command_docs(interaction.client)

            await interaction.followup.send(
                f"Synced {len(result.command_names)} {scope}: "
                f"{format_command_names(result.command_names)}{docs_note}",
                ephemeral=True,
            )

    @admin_group.command(
        name="diagnostics",
        description="Show bot health information.",
    )
    async def diagnostics(interaction: discord.Interaction) -> None:
        async with command_context(interaction, "admin diagnostics"):
            if not await _require_admin_or_owner(interaction):
                return

            if not await defer_ephemeral(interaction, "admin diagnostics"):
                return

            summaries_path = Path(settings.summaries_dir)
            md_count = sum(1 for _ in summaries_path.rglob("*.md"))
            command_names = registered_command_names(interaction.client.tree)

            bot_user = str(interaction.client.user) if interaction.client.user else "Unknown"
            bot_user_id = getattr(interaction.client.user, "id", None)

            text = _build_diagnostics_text(
                bot_user=bot_user,
                bot_user_id=bot_user_id,
                summaries_dir=settings.summaries_dir,
                md_count=md_count,
                command_names=command_names,
                forum_channel_id=settings.club_night_forum_channel_id,
                owner_count=len(settings.bot_owner_user_ids),
                cache_age=cache_age_seconds(),
                git_commit=os.environ.get("GIT_COMMIT", "unknown"),
            )

            await interaction.followup.send(text, ephemeral=True)

    @admin_group.command(
        name="config-check",
        description="Verify bot configuration is valid.",
    )
    async def config_check(interaction: discord.Interaction) -> None:
        async with command_context(interaction, "admin config-check"):
            if not await _require_admin_or_owner(interaction):
                return

            if not await defer_ephemeral(interaction, "admin config-check"):
                return

            summaries_path = Path(settings.summaries_dir)
            summaries_ok = summaries_path.exists() and any(summaries_path.rglob("*.md"))

            template_path = Path(settings.club_night_announcement_template_path)
            template_ok = (
                template_path.exists()
                and template_path.is_file()
                and template_path.stat().st_size > 0
            )

            forum_channel_ok = False
            forum_tag_ok: bool | None = None

            channel_id = settings.club_night_forum_channel_id
            try:
                channel = interaction.client.get_channel(channel_id) or await interaction.client.fetch_channel(channel_id)
                if isinstance(channel, discord.ForumChannel):
                    forum_channel_ok = True
                    if settings.club_night_forum_tag_id is not None:
                        valid_ids = {tag.id for tag in channel.available_tags}
                        forum_tag_ok = settings.club_night_forum_tag_id in valid_ids
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

            text = _build_config_check_text(
                summaries_ok=summaries_ok,
                template_ok=template_ok,
                forum_channel_ok=forum_channel_ok,
                forum_tag_ok=forum_tag_ok,
            )

            await interaction.followup.send(text, ephemeral=True)

    tree.add_command(admin_group)


async def _update_command_docs(client: discord.Client) -> str:
    """Refresh the community command-docs post; return a note for the reply.

    Best-effort: a failure here must never break command syncing, so any error
    is logged and surfaced as a warning line rather than raised.
    """
    channel_id = settings.command_docs_channel_id
    if channel_id is None:
        return ""

    try:
        result = await publish_command_docs(client, channel_id, client.tree)
    except Exception:
        logging.exception("Failed to update the command docs post in channel %s.", channel_id)
        return f"\n⚠️ Could not update the command docs post in <#{channel_id}> — see logs."

    logging.info(
        "Updated command docs post in channel %s: %d message(s), %d replaced.",
        channel_id,
        len(result.message_ids),
        result.deleted,
    )
    message_count = len(result.message_ids)
    plural = "message" if message_count == 1 else "messages"
    return f"\nUpdated the command docs post in <#{channel_id}> ({message_count} {plural})."


async def _require_admin_or_owner(interaction: discord.Interaction) -> bool:
    if _is_admin_or_owner(interaction):
        return True

    logging.warning(
        "Rejected /admin command from user_id=%s without runtime admin or owner permission.",
        getattr(interaction.user, "id", "unknown"),
    )
    message = "You need Discord administrator permission or bot owner access to use this command."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

    return False


def _is_admin_or_owner(interaction: discord.Interaction) -> bool:
    user_id = getattr(interaction.user, "id", None)

    if user_id in settings.bot_owner_user_ids:
        return True

    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False))
