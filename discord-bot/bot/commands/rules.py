import logging
from typing import Optional

import discord
from discord import app_commands

from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import check_rate_limit, command_context, defer_ephemeral
from bot.config import settings
from bot.services.rules_query import (
    RulesQueryError,
    RulesQueryHit,
    format_rules_hit,
    format_rules_query,
    result_option_description,
    result_option_label,
    search_rules,
)


class _RulesResultSelect(discord.ui.Select):
    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, RulesResultsView)
        await view.show(interaction, int(self.values[0]))


class RulesResultsView(discord.ui.View):
    def __init__(
        self,
        owner_user_id: int,
        query: str,
        hits: list[RulesQueryHit],
        *,
        selected: int = 0,
    ) -> None:
        super().__init__(timeout=180)
        self._owner_user_id = owner_user_id
        self._query = query
        self._hits = hits
        self.message: Optional[discord.WebhookMessage] = None
        self._select = _RulesResultSelect(placeholder="Show another result…")
        self.add_item(self._select)
        self._refresh_options(selected)

    def _refresh_options(self, selected: int) -> None:
        self._select.options = [
            discord.SelectOption(
                label=result_option_label(hit),
                value=str(index),
                description=result_option_description(hit),
                default=index == selected,
            )
            for index, hit in enumerate(self._hits)
        ]

    async def show(self, interaction: discord.Interaction, index: int) -> None:
        if getattr(interaction.user, "id", None) != self._owner_user_id:
            await interaction.response.send_message(
                "Only the user who ran this query can browse its results.",
                ephemeral=True,
            )
            return
        self._refresh_options(index)
        await interaction.response.edit_message(
            content=format_rules_hit(self._query, self._hits[index]),
            view=self,
            allowed_mentions=NO_MENTIONS,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            await self.message.edit(view=self)


def register_rules_commands(tree: discord.app_commands.CommandTree) -> None:
    rules_group = app_commands.Group(
        name="rules",
        description="Look up Horus Heresy rules from the rules corpus.",
    )

    @rules_group.command(
        name="query",
        description="Search the rules for the answer to a question.",
    )
    @app_commands.describe(
        query="What rule do you need? For example a keyword, action, or weapon type.",
    )
    async def query(
        interaction: discord.Interaction,
        query: str,
    ) -> None:
        async with command_context(interaction, "rules query"):
            if not await check_rate_limit(interaction, "rules query"):
                return
            if not await defer_ephemeral(interaction, "rules query", ephemeral=True):
                return

            try:
                hits = search_rules(settings.summaries_dir, query)
            except RulesQueryError as exc:
                logging.exception("Could not search the rules corpus.")
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            if not hits:
                await interaction.followup.send(
                    format_rules_query(query, hits),
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            view = (
                RulesResultsView(getattr(interaction.user, "id", 0), query, hits)
                if len(hits) > 1
                else None
            )
            message = await interaction.followup.send(
                format_rules_hit(query, hits[0]),
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
                view=view if view is not None else discord.utils.MISSING,
                wait=True,
            )
            if view is not None:
                view.message = message

    tree.add_command(rules_group)
