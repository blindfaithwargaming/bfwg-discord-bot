import logging
from typing import Optional

import discord
from discord import app_commands

from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import check_rate_limit, command_context, defer_ephemeral
from bot.config import settings
from bot.services.mission_generator import (
    MissionGeneratorError,
    format_mission,
    generate_mission,
)


class MissionRerollView(discord.ui.View):
    def __init__(
        self,
        owner_user_id: int,
        deployment: Optional[str],
        titans: bool,
        twist: bool,
        max_secondary_vp: Optional[int],
    ) -> None:
        super().__init__(timeout=180)
        self._owner_user_id = owner_user_id
        self._deployment = deployment
        self._titans = titans
        self._twist = twist
        self._max_secondary_vp = max_secondary_vp
        self._counter = 0
        self.message: Optional[discord.WebhookMessage] = None

    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.secondary)
    async def reroll_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if getattr(interaction.user, "id", None) != self._owner_user_id:
            await interaction.response.send_message(
                "Only the user who generated this mission can reroll it.",
                ephemeral=True,
            )
            return

        self._counter += 1
        try:
            result = generate_mission(
                settings.summaries_dir,
                deployment_slug=self._deployment,
                titans=self._titans,
                include_twist=self._twist,
                max_secondary_points=self._max_secondary_vp,
            )
        except MissionGeneratorError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.edit_message(
            content=format_mission(result),
            allowed_mentions=NO_MENTIONS,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            await self.message.edit(view=self)


def register_mission_commands(tree: discord.app_commands.CommandTree) -> None:
    mission_group = app_commands.Group(
        name="mission",
        description="Generate a playable Horus Heresy mission.",
    )

    @mission_group.command(
        name="random",
        description="Randomly generate a playable HH3-compatible mission.",
    )
    @app_commands.describe(
        deployment="Deployment zone name (e.g. dawn-of-war). Chosen randomly if omitted.",
        titans="Include Engine Kill (Titan) missions in the primary mission pool.",
        twist="Add a random narrative twist to the mission.",
        max_secondary_vp="Maximum VP per secondary objective (default: 4, must be at least 1).",
    )
    async def random_mission(
        interaction: discord.Interaction,
        deployment: Optional[str] = None,
        titans: Optional[bool] = False,
        twist: Optional[bool] = False,
        max_secondary_vp: Optional[int] = None,
    ) -> None:
        async with command_context(interaction, "mission random"):
            if not await check_rate_limit(interaction, "mission random"):
                return
            if not await defer_ephemeral(interaction, "mission random", ephemeral=True):
                return

            try:
                result = generate_mission(
                    settings.summaries_dir,
                    deployment_slug=deployment,
                    titans=bool(titans),
                    include_twist=bool(twist),
                    max_secondary_points=max_secondary_vp,
                )
            except MissionGeneratorError as exc:
                logging.exception("Could not generate mission.")
                await interaction.followup.send(
                    str(exc),
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            view = MissionRerollView(
                getattr(interaction.user, "id", 0),
                deployment,
                bool(titans),
                bool(twist),
                max_secondary_vp,
            )
            msg = await interaction.followup.send(
                format_mission(result),
                allowed_mentions=NO_MENTIONS,
                view=view,
                wait=True,
            )
            view.message = msg

    tree.add_command(mission_group)
