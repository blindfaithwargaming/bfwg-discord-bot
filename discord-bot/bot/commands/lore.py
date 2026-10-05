import logging
from typing import Optional

import discord
from discord import app_commands

from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import check_rate_limit, command_context, defer_ephemeral
from bot.config import settings
from bot.services.faction_index import autocomplete_faction
from bot.services.lore_search import (
    LoreSearchError,
    format_lore_search,
    search_lore,
)
from bot.services.random_lore import (
    RandomLoreError,
    format_random_lore,
    generate_random_lore,
)


def register_lore_commands(tree: discord.app_commands.CommandTree) -> None:
    lore_group = app_commands.Group(
        name="lore",
        description="Generate lore prompts from the campaign summaries.",
    )

    async def _faction_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        matches = autocomplete_faction(str(settings.summaries_dir), current)
        return [app_commands.Choice(name=m, value=m) for m in matches]

    @lore_group.command(
        name="random",
        description="Generate a random lore hook from the summaries.",
    )
    @app_commands.describe(
        faction="Optional faction, legion, warband, or force to focus the lore hook.",
    )
    @app_commands.autocomplete(faction=_faction_autocomplete)
    async def random_lore(
        interaction: discord.Interaction,
        faction: Optional[str] = None,
    ) -> None:
        async with command_context(interaction, "lore random"):
            if not await check_rate_limit(interaction, "lore random"):
                return
            if not await defer_ephemeral(interaction, "lore random", ephemeral=False):
                return

            try:
                lore = generate_random_lore(settings.summaries_dir, faction)
            except RandomLoreError as exc:
                logging.exception("Could not generate random lore hook.")
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            await interaction.followup.send(format_random_lore(lore), allowed_mentions=NO_MENTIONS)

    @lore_group.command(
        name="search",
        description="Search the lore summaries for matching excerpts.",
    )
    @app_commands.describe(
        query="Search text, for example a faction, campaign, character, location, or theme.",
    )
    async def search(
        interaction: discord.Interaction,
        query: str,
    ) -> None:
        async with command_context(interaction, "lore search"):
            if not await check_rate_limit(interaction, "lore search"):
                return
            if not await defer_ephemeral(interaction, "lore search", ephemeral=False):
                return

            try:
                hits = search_lore(settings.summaries_dir, query)
            except LoreSearchError as exc:
                logging.exception("Could not search lore summaries.")
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            await interaction.followup.send(format_lore_search(query, hits), allowed_mentions=NO_MENTIONS)

    tree.add_command(lore_group)
