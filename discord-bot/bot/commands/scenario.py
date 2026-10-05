import logging
from typing import Optional

import discord
from discord import app_commands

from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import check_rate_limit, command_context, defer_ephemeral
from bot.config import settings
from bot.services.faction_index import autocomplete_faction
from bot.services.scenario_seed import (
    ScenarioSeedError,
    format_scenario_seed,
    generate_scenario_seed,
    generate_scenario_twist,
)


class RerollView(discord.ui.View):
    def __init__(
        self,
        owner_user_id: int,
        attacker: str,
        defender: str,
        tone: Optional[str],
        mission_type: Optional[str] = None,
        theatre: Optional[str] = None,
    ) -> None:
        super().__init__(timeout=180)
        self._owner_user_id = owner_user_id
        self._attacker = attacker
        self._defender = defender
        self._tone = tone
        self._mission_type = mission_type
        self._theatre = theatre
        self._counter = 0
        self.message: Optional[discord.WebhookMessage] = None

    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.secondary)
    async def reroll_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if getattr(interaction.user, "id", None) != self._owner_user_id:
            await interaction.response.send_message(
                "Only the user who generated this scenario can reroll it.",
                ephemeral=True,
            )
            return
        self._counter += 1
        try:
            scenario = generate_scenario_seed(
                settings.summaries_dir,
                self._attacker,
                self._defender,
                self._tone,
                reroll=self._counter,
                mission_type=self._mission_type,
                theatre=self._theatre,
            )
        except ScenarioSeedError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(
            content=format_scenario_seed(scenario),
            allowed_mentions=NO_MENTIONS,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            await self.message.edit(view=self)


def register_scenario_commands(tree: discord.app_commands.CommandTree) -> None:
    scenario_group = app_commands.Group(
        name="scenario",
        description="Generate Horus Heresy scenario tools.",
    )

    async def _faction_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        matches = autocomplete_faction(str(settings.summaries_dir), current)
        return [app_commands.Choice(name=m, value=m) for m in matches]

    @scenario_group.command(
        name="seed",
        description="Generate a short mission seed from the lore summaries.",
    )
    @app_commands.describe(
        attacker="Attacking faction, legion, warband, or force.",
        defender="Defending faction, legion, warband, or force.",
        tone="Optional tone, for example grim, desperate, heroic, horror, siege, or traitor.",
        mission_type="Optional mission type, e.g. decapitation strike or zone mortalis raid.",
        theatre="Optional theatre, e.g. a shattered orbital dock or a fortress-city outer curtain.",
    )
    @app_commands.autocomplete(attacker=_faction_autocomplete, defender=_faction_autocomplete)
    async def seed(
        interaction: discord.Interaction,
        attacker: str,
        defender: str,
        tone: Optional[str] = None,
        mission_type: Optional[str] = None,
        theatre: Optional[str] = None,
    ) -> None:
        async with command_context(interaction, "scenario seed"):
            if not await check_rate_limit(interaction, "scenario seed"):
                return
            if not await defer_ephemeral(interaction, "scenario seed", ephemeral=False):
                return

            try:
                scenario = generate_scenario_seed(
                    settings.summaries_dir,
                    attacker,
                    defender,
                    tone,
                    mission_type=mission_type,
                    theatre=theatre,
                )
            except ScenarioSeedError as exc:
                logging.exception("Could not generate scenario seed.")
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            view = RerollView(
                getattr(interaction.user, "id", 0),
                attacker,
                defender,
                tone,
                mission_type=mission_type,
                theatre=theatre,
            )
            msg = await interaction.followup.send(
                format_scenario_seed(scenario),
                allowed_mentions=NO_MENTIONS,
                view=view,
                wait=True,
            )
            view.message = msg

    @scenario_group.command(
        name="twist",
        description="Get a random battlefield complication.",
    )
    @app_commands.describe(
        tone="Optional tone to colour the complication: grim, desperate, heroic, horror, siege, or traitor.",
    )
    async def twist(
        interaction: discord.Interaction,
        tone: Optional[str] = None,
    ) -> None:
        async with command_context(interaction, "scenario twist"):
            if not await check_rate_limit(interaction, "scenario twist"):
                return
            if not await defer_ephemeral(interaction, "scenario twist"):
                return

            complication = generate_scenario_twist(tone)
            await interaction.followup.send(
                f"**Twist:** {complication}",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )

    tree.add_command(scenario_group)
