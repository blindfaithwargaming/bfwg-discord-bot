import logging

import discord
from discord import app_commands

from bot.commands.club_night_group import _club_night_group
from bot.commands.club_night_pairings import (
    CONSTRAINT_STORE_ERROR_MESSAGE,
    CONSTRAINTS_ERROR_MESSAGE,
    NO_MENTIONS,
    PairingsPreviewView,
    STORE_ERROR_MESSAGE,
    _constraints_store,
    _load_constraints,
    _pairing_history_store,
    _resolve_club_night,
    _resolve_guild_id,
    _signup_store,
    _signup_faction_autocomplete,
    _GAME_CHOICES,
)
from bot.commands.interaction_helpers import (
    check_rate_limit,
    command_context,
    defer_ephemeral,
    require_admin_or_owner,
)
from bot.config import settings
from bot.services.club_night_pairings import generate_pairings
from bot.services.club_night_signups import (
    DEFAULT_SIDE,
    FORMAT_PREF_LABELS,
    SIDE_LABELS,
)
from bot.services.discord_text import escape_public_text
from bot.services.stadium_events import ClubNight, format_date
from bot.services.organised_play import (
    PLAY_STYLES,
    signup_options,
    build_signup_instructions,
    select_game,
    game_label,
)

_SIDE_CHOICES = [
    app_commands.Choice(name="Loyalist", value="loyalist"),
    app_commands.Choice(name="Traitor", value="traitor"),
    app_commands.Choice(name="Either", value="either"),
]

_FORMAT_CHOICES = [
    app_commands.Choice(name="Solo (no teammate; may face a team)", value="solo"),
    app_commands.Choice(name="Doubles (happy to team up)", value="doubles"),
    app_commands.Choice(name="Either (team up if it helps)", value="either"),
]

SIGNUP_INFO_FORBIDDEN_MESSAGE = (
    "I don't have permission to post in this thread, so the signup procedure "
    "wasn't posted."
)
SIGNUP_INFO_FAILED_MESSAGE = (
    "Discord rejected the signup procedure post. Please try again shortly."
)


async def _send(interaction: discord.Interaction, message: str) -> None:
    await interaction.followup.send(
        message,
        ephemeral=True,
        allowed_mentions=NO_MENTIONS,
    )


def _member_name(member: discord.Member) -> str:
    return str(getattr(member, "display_name", None) or member)


def _side_label(value: str) -> str:
    return SIDE_LABELS.get(value, "Either")


def _format_label(value: str) -> str:
    return FORMAT_PREF_LABELS.get(value, FORMAT_PREF_LABELS["solo"])


def _validate_pair(
    player1: discord.Member, player2: discord.Member
) -> str | None:
    if player1.id == player2.id:
        return "Pick two different players."
    if player1.bot or player2.bot:
        return "You can't include a bot in a pairing."
    return None


class ClearSignupsConfirmView(discord.ui.View):
    """Confirmation gate for wiping a club night's sign-ups and constraints.

    Owner-scoped and disabled on the 180s timeout. Only the Confirm press
    performs the wipe — there is no destructive default.
    """

    def __init__(
        self,
        *,
        owner_user_id: int,
        guild_id: str,
        event_date,
        title: str,
        club_night: ClubNight | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self._owner_user_id = owner_user_id
        self._guild_id = guild_id
        self._event_date = event_date
        self._title = title
        self._club_night = club_night
        self.message: discord.WebhookMessage | None = None

    async def _reject_non_owner(self, interaction: discord.Interaction) -> bool:
        if getattr(interaction.user, "id", None) == self._owner_user_id:
            return False
        await interaction.response.send_message(
            "Only the organiser who started this can confirm it.",
            ephemeral=True,
        )
        return True

    @discord.ui.button(label="Confirm wipe", style=discord.ButtonStyle.danger)
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await self._reject_non_owner(interaction):
            return

        for item in self.children:
            item.disabled = True

        try:
            _constraints_store(self._club_night).clear_event(
                self._guild_id, self._event_date
            )
        except OSError:
            logging.exception("Could not clear club night pairing constraints.")
            await interaction.response.edit_message(
                content=CONSTRAINT_STORE_ERROR_MESSAGE,
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
            return

        try:
            removed = _signup_store(self._club_night).clear_event(
                self._guild_id, self._event_date
            )
        except OSError:
            logging.exception("Could not clear club night sign-ups.")
            await interaction.response.edit_message(
                content=STORE_ERROR_MESSAGE,
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
            return

        word = "sign-up" if removed == 1 else "sign-ups"
        await interaction.response.edit_message(
            content=(
                f"Cleared {removed} {word} and all pairing constraints for "
                f"{escape_public_text(self._title)}."
            ),
            view=self,
            allowed_mentions=NO_MENTIONS,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await self._reject_non_owner(interaction):
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Cancelled — nothing was cleared.",
            view=self,
            allowed_mentions=NO_MENTIONS,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            await self.message.edit(view=self)


@_club_night_group.command(
    name="add-player",
    description="Admin: sign a member up for this event.",
)
@app_commands.describe(
    member="The member to sign up.",
    side="The side they will play (default: either).",
    format="Heresy doubles choice (keeps their last choice, or solo).",
    faction="Optional faction.",
    play_style="Optional Learning, Casual or Competitive preference.",
    rice="Bushido only: preferred rice level (usual level: 100).",
    game="Game to play; omit to use the thread title. Replaces their signup for this night.",
)
@app_commands.autocomplete(faction=_signup_faction_autocomplete)
@app_commands.choices(
    game=_GAME_CHOICES,
    side=_SIDE_CHOICES,
    format=_FORMAT_CHOICES,
    play_style=[app_commands.Choice(name=v, value=k) for k, v in PLAY_STYLES.items()],
)
async def club_night_add_player(
    interaction: discord.Interaction,
    member: discord.Member,
    side: app_commands.Choice[str] | None = None,
    format: app_commands.Choice[str] | None = None,
    faction: str | None = None,
    play_style: app_commands.Choice[str] | None = None,
    rice: app_commands.Range[int, 1, 1000] | None = None,
    game: app_commands.Choice[str] | None = None,
) -> None:
    async with command_context(interaction, "club-night add-player"):
        if not await require_admin_or_owner(interaction, "club-night add-player"):
            return
        if not await check_rate_limit(interaction, "club-night add-player"):
            return
        if not await defer_ephemeral(interaction, "club-night add-player"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        if member.bot:
            await _send(interaction, "You can't sign up a bot.")
            return

        try:
            selected_game = select_game(club_night, game.value if game else None)
            details = signup_options(
                game=selected_game,
                faction=faction,
                play_style=play_style.value if play_style else None,
                rice=rice,
                side=side.value if side else None,
                format_pref=format.value if format else None,
            )
        except ValueError as exc:
            await _send(interaction, str(exc))
            return

        side_value = side.value if side is not None else DEFAULT_SIDE
        format_value = format.value if format is not None else None
        display_name = _member_name(member)
        store = _signup_store(club_night)
        try:
            store.add_signup(
                guild_id,
                club_night.event_date,
                str(member.id),
                display_name,
                side=side_value,
                format_pref=format_value,
                details=details,
            )
            count = len(store.get_signups(guild_id, club_night.event_date))
        except OSError:
            logging.exception("Could not persist admin club night sign-up.")
            await _send(interaction, STORE_ERROR_MESSAGE)
            return

        label = SIDE_LABELS.get(side_value)
        side_text = f" as {label}" if label else ""
        await _send(
            interaction,
            f"Signed up {escape_public_text(display_name)}{side_text} for "
            f"{club_night.title} ({format_date(club_night.event_date)}). "
            f"Playing {escape_public_text(game_label(selected_game))}. {count} attending.",
        )


@_club_night_group.command(
    name="remove-player",
    description="Admin: remove a member's sign-up for this event.",
)
@app_commands.describe(member="The member to remove.")
async def club_night_remove_player(
    interaction: discord.Interaction,
    member: discord.Member,
) -> None:
    async with command_context(interaction, "club-night remove-player"):
        if not await require_admin_or_owner(interaction, "club-night remove-player"):
            return
        if not await check_rate_limit(interaction, "club-night remove-player"):
            return
        if not await defer_ephemeral(interaction, "club-night remove-player"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        name = escape_public_text(_member_name(member))
        try:
            removed = _signup_store(club_night).remove_signup(
                guild_id, club_night.event_date, str(member.id)
            )
        except OSError:
            logging.exception("Could not persist admin club night removal.")
            await _send(interaction, STORE_ERROR_MESSAGE)
            return

        if removed:
            await _send(interaction, f"Removed {name}'s sign-up.")
        else:
            await _send(interaction, f"{name} wasn't signed up.")


@_club_night_group.command(
    name="set-side",
    description="Admin: set a signed-up member's side for this event.",
)
@app_commands.describe(member="The signed-up member.", side="The side to set.")
@app_commands.choices(side=_SIDE_CHOICES)
async def club_night_set_side(
    interaction: discord.Interaction,
    member: discord.Member,
    side: app_commands.Choice[str],
) -> None:
    async with command_context(interaction, "club-night set-side"):
        if not await require_admin_or_owner(interaction, "club-night set-side"):
            return
        if not await check_rate_limit(interaction, "club-night set-side"):
            return
        if not await defer_ephemeral(interaction, "club-night set-side"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        name = escape_public_text(_member_name(member))
        try:
            _signup_store(club_night).set_side(
                guild_id, club_night.event_date, str(member.id), side.value
            )
        except ValueError as exc:
            await _send(interaction, str(exc))
            return
        except KeyError:
            await _send(
                interaction,
                f"{name} isn't signed up — use `/club-night add-player` first.",
            )
            return
        except OSError:
            logging.exception("Could not persist admin side change.")
            await _send(interaction, STORE_ERROR_MESSAGE)
            return

        await _send(interaction, f"Set {name}'s side to {_side_label(side.value)}.")


@_club_night_group.command(
    name="set-format",
    description="Admin: set a signed-up member's doubles format for this event.",
)
@app_commands.describe(
    member="The signed-up member.",
    format="Solo (no teammate), Doubles (team up), or Either (team up if it helps).",
)
@app_commands.choices(format=_FORMAT_CHOICES)
async def club_night_set_format(
    interaction: discord.Interaction,
    member: discord.Member,
    format: app_commands.Choice[str],
) -> None:
    async with command_context(interaction, "club-night set-format"):
        if not await require_admin_or_owner(interaction, "club-night set-format"):
            return
        if not await check_rate_limit(interaction, "club-night set-format"):
            return
        if not await defer_ephemeral(interaction, "club-night set-format"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        name = escape_public_text(_member_name(member))
        try:
            _signup_store(club_night).set_format(
                guild_id, club_night.event_date, str(member.id), format.value
            )
        except ValueError as exc:
            await _send(interaction, str(exc))
            return
        except KeyError:
            await _send(
                interaction,
                f"{name} isn't signed up — use `/club-night add-player` first.",
            )
            return
        except OSError:
            logging.exception("Could not persist admin format change.")
            await _send(interaction, STORE_ERROR_MESSAGE)
            return

        await _send(
            interaction, f"Set {name}'s format to {_format_label(format.value)}."
        )


@_club_night_group.command(
    name="lock-pair",
    description="Admin: force two players to be paired for this event.",
)
@app_commands.describe(
    player1="First player.",
    player2="Second player.",
    clear="Remove the lock instead of setting one.",
)
async def club_night_lock_pair(
    interaction: discord.Interaction,
    player1: discord.Member,
    player2: discord.Member,
    clear: bool = False,
) -> None:
    async with command_context(interaction, "club-night lock-pair"):
        if not await require_admin_or_owner(interaction, "club-night lock-pair"):
            return
        if not await check_rate_limit(interaction, "club-night lock-pair"):
            return
        if not await defer_ephemeral(interaction, "club-night lock-pair"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        rejection = _validate_pair(player1, player2)
        if rejection is not None:
            await _send(interaction, rejection)
            return

        n1 = escape_public_text(_member_name(player1))
        n2 = escape_public_text(_member_name(player2))
        store = _constraints_store(club_night)
        try:
            if clear:
                store.remove_lock(
                    guild_id, club_night.event_date, str(player1.id), str(player2.id)
                )
                message = f"Lock removed: {n1} and {n2} are no longer forced together."
            else:
                store.add_lock(
                    guild_id, club_night.event_date, str(player1.id), str(player2.id)
                )
                message = f"Locked: {n1} and {n2} will be paired together."
        except OSError:
            logging.exception("Could not persist pairing lock.")
            await _send(interaction, CONSTRAINT_STORE_ERROR_MESSAGE)
            return

        await _send(interaction, message)


@_club_night_group.command(
    name="avoid-pair",
    description="Admin: avoid pairing two players for this event.",
)
@app_commands.describe(
    player1="First player.",
    player2="Second player.",
    clear="Remove the avoid instead of setting one.",
)
async def club_night_avoid_pair(
    interaction: discord.Interaction,
    player1: discord.Member,
    player2: discord.Member,
    clear: bool = False,
) -> None:
    async with command_context(interaction, "club-night avoid-pair"):
        if not await require_admin_or_owner(interaction, "club-night avoid-pair"):
            return
        if not await check_rate_limit(interaction, "club-night avoid-pair"):
            return
        if not await defer_ephemeral(interaction, "club-night avoid-pair"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        rejection = _validate_pair(player1, player2)
        if rejection is not None:
            await _send(interaction, rejection)
            return

        n1 = escape_public_text(_member_name(player1))
        n2 = escape_public_text(_member_name(player2))
        store = _constraints_store(club_night)
        try:
            if clear:
                store.remove_avoid(
                    guild_id, club_night.event_date, str(player1.id), str(player2.id)
                )
                message = f"Avoid removed: {n1} and {n2} may be paired again."
            else:
                store.add_avoid(
                    guild_id, club_night.event_date, str(player1.id), str(player2.id)
                )
                message = (
                    f"Avoid set: {n1} and {n2} won't be paired tonight if it can be "
                    "helped (best-effort)."
                )
        except OSError:
            logging.exception("Could not persist pairing avoid.")
            await _send(interaction, CONSTRAINT_STORE_ERROR_MESSAGE)
            return

        await _send(interaction, message)


@_club_night_group.command(
    name="set-bye",
    description="Admin: force a member to sit out this event's pairings.",
)
@app_commands.describe(
    member="The member to bye (omit when clearing).",
    clear="Remove the forced bye instead of setting one.",
)
async def club_night_set_bye(
    interaction: discord.Interaction,
    member: discord.Member | None = None,
    clear: bool = False,
) -> None:
    async with command_context(interaction, "club-night set-bye"):
        if not await require_admin_or_owner(interaction, "club-night set-bye"):
            return
        if not await check_rate_limit(interaction, "club-night set-bye"):
            return
        if not await defer_ephemeral(interaction, "club-night set-bye"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        store = _constraints_store(club_night)
        try:
            if clear:
                store.clear_bye(guild_id, club_night.event_date)
                await _send(interaction, "Forced bye cleared.")
                return

            if member is None:
                await _send(
                    interaction,
                    "Choose a member to bye, or use `clear:true` to remove the bye.",
                )
                return
            if member.bot:
                await _send(interaction, "You can't bye a bot.")
                return

            store.set_bye(guild_id, club_night.event_date, str(member.id))
        except OSError:
            logging.exception("Could not persist forced bye.")
            await _send(interaction, CONSTRAINT_STORE_ERROR_MESSAGE)
            return

        name = escape_public_text(_member_name(member))
        await _send(interaction, f"{name} will sit out the next pairings (forced bye).")


@_club_night_group.command(
    name="clear-signups",
    description="Admin: wipe all sign-ups and constraints for this event.",
)
async def club_night_clear_signups(interaction: discord.Interaction) -> None:
    async with command_context(interaction, "club-night clear-signups"):
        if not await require_admin_or_owner(interaction, "club-night clear-signups"):
            return
        if not await check_rate_limit(interaction, "club-night clear-signups"):
            return
        if not await defer_ephemeral(interaction, "club-night clear-signups"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        view = ClearSignupsConfirmView(
            owner_user_id=getattr(interaction.user, "id", 0),
            guild_id=guild_id,
            event_date=club_night.event_date,
            title=club_night.title,
            club_night=club_night,
        )
        message = await interaction.followup.send(
            "This wipes **all sign-ups and pairing constraints** for "
            f"{club_night.title} ({format_date(club_night.event_date)}). "
            "This cannot be undone.",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
            view=view,
            wait=True,
        )
        view.message = message


@_club_night_group.command(
    name="preview-pairings",
    description="Admin: privately preview and reshuffle pairings before posting.",
)
async def club_night_preview_pairings(interaction: discord.Interaction) -> None:
    async with command_context(interaction, "club-night preview-pairings"):
        if not await require_admin_or_owner(
            interaction, "club-night preview-pairings"
        ):
            return
        if not await check_rate_limit(interaction, "club-night preview-pairings"):
            return
        if not await defer_ephemeral(interaction, "club-night preview-pairings"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        try:
            signups = _signup_store(club_night).get_signups(
                guild_id, club_night.event_date
            )
        except OSError:
            logging.exception("Could not read club night sign-ups.")
            await _send(interaction, STORE_ERROR_MESSAGE)
            return

        if len(signups) < 2:
            count = len(signups)
            player_word = "player" if count == 1 else "players"
            await _send(
                interaction,
                f"Only {count} {player_word} signed up — nothing to pair.",
            )
            return

        history_store = _pairing_history_store(club_night)
        try:
            recent_opponents = history_store.get_recent_for_signups(
                guild_id,
                club_night.event_date,
                settings.club_night_pairing_lookback,
                signups,
            )
        except OSError:
            logging.exception("Could not read club night pairing history.")
            recent_opponents = frozenset()

        try:
            constraints = _load_constraints(guild_id, club_night.event_date, club_night)
        except OSError:
            logging.exception("Could not read club night pairing constraints.")
            await _send(interaction, CONSTRAINTS_ERROR_MESSAGE)
            return

        result = generate_pairings(
            signups,
            recent_opponents=recent_opponents,
            locked_pairs=constraints.locked_pairs,
            avoided_pairs=constraints.avoided_pairs,
            forced_bye=constraints.forced_bye,
        )

        view = PairingsPreviewView(
            owner_user_id=getattr(interaction.user, "id", 0),
            guild_id=guild_id,
            event_date=club_night.event_date,
            signups=signups,
            recent_opponents=recent_opponents,
            constraints=constraints,
            history_store=history_store,
            result=result,
        )
        message = await interaction.followup.send(
            view.render(),
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
            view=view,
            wait=True,
        )
        view.message = message


def _build_signup_instructions_text(*, club_night: ClubNight) -> str:
    return build_signup_instructions(event=club_night)


@_club_night_group.command(
    name="signup-info",
    description="Admin: post the signup procedure in this event thread.",
)
async def club_night_signup_info(interaction: discord.Interaction) -> None:
    async with command_context(interaction, "club-night signup-info"):
        if not await require_admin_or_owner(interaction, "club-night signup-info"):
            return
        if not await check_rate_limit(interaction, "club-night signup-info"):
            return
        if not await defer_ephemeral(interaction, "club-night signup-info"):
            return
        if await _resolve_guild_id(interaction) is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return
        try:
            await interaction.channel.send(
                _build_signup_instructions_text(club_night=club_night),
                allowed_mentions=NO_MENTIONS,
            )
        except discord.Forbidden:
            await _send(interaction, SIGNUP_INFO_FORBIDDEN_MESSAGE)
            return
        except discord.HTTPException:
            logging.exception("Could not post signup procedure.")
            await _send(interaction, SIGNUP_INFO_FAILED_MESSAGE)
            return
        await _send(interaction, "Signup procedure posted.")
