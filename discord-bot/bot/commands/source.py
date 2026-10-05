import logging

import discord
from discord import app_commands

from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import check_rate_limit, command_context, defer_ephemeral
from bot.config import settings
from bot.services.lore_search import (
    LoreSearchError,
    format_source_results,
    search_lore,
)


def register_source_command(tree: discord.app_commands.CommandTree) -> None:
    @tree.command(
        name="source",
        description="Find summary source files for a lore query.",
    )
    @app_commands.describe(
        query="Search text, for example a faction, campaign, character, location, or theme.",
    )
    async def source(interaction: discord.Interaction, query: str) -> None:
        async with command_context(interaction, "source"):
            if not await check_rate_limit(interaction, "source"):
                return
            if not await defer_ephemeral(interaction, "source", ephemeral=False):
                return

            try:
                hits = search_lore(settings.summaries_dir, query)
            except LoreSearchError as exc:
                logging.exception("Could not look up lore sources.")
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            await interaction.followup.send(format_source_results(query, hits), allowed_mentions=NO_MENTIONS)
