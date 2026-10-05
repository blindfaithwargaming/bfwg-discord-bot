import discord
from discord import app_commands

from bot.commands.interaction_helpers import command_context


def register_ping_command(tree: discord.app_commands.CommandTree) -> None:
    @tree.command(name="ping", description="Check whether the bot is responding.")
    @app_commands.default_permissions(administrator=True)
    async def ping(interaction: discord.Interaction) -> None:
        async with command_context(interaction, "ping"):
            await interaction.response.send_message("Pong.", ephemeral=True)
