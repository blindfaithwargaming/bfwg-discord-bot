import logging

import discord
from discord import app_commands

from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import check_rate_limit, command_context, defer_ephemeral
from bot.config import settings
from bot.services.faction_index import autocomplete_faction
from bot.services.name_generator import (
    CATEGORIES,
    NameGeneratorError,
    format_names,
    generate_names,
)


def register_name_commands(tree: discord.app_commands.CommandTree) -> None:
    name_group = app_commands.Group(
        name="name",
        description="Generate Horus Heresy names for your forces.",
    )

    async def _faction_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        matches = autocomplete_faction(str(settings.summaries_dir), current)
        return [app_commands.Choice(name=m, value=m) for m in matches]

    @name_group.command(
        name="generator",
        description="Generate names for commanders, ships, warbands, relics, or locations.",
    )
    @app_commands.describe(
        faction="The faction or legion to generate names for.",
        category="The type of name to generate.",
    )
    @app_commands.autocomplete(faction=_faction_autocomplete)
    @app_commands.choices(
        category=[
            app_commands.Choice(name="Commander", value="commander"),
            app_commands.Choice(name="Ship", value="ship"),
            app_commands.Choice(name="Warband", value="warband"),
            app_commands.Choice(name="Relic", value="relic"),
            app_commands.Choice(name="Location", value="location"),
        ]
    )
    async def generator(
        interaction: discord.Interaction,
        faction: str,
        category: str,
    ) -> None:
        async with command_context(interaction, "name generator"):
            if not await check_rate_limit(interaction, "name generator"):
                return
            if not await defer_ephemeral(interaction, "name generator"):
                return

            try:
                names = generate_names(faction, category)
            except NameGeneratorError as exc:
                logging.exception("Could not generate names.")
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            await interaction.followup.send(
                format_names(faction, category, names),
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )

    tree.add_command(name_group)
