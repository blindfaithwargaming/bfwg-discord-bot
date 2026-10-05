import logging
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands

from bot.commands.club_night_group import _club_night_group
from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import (
    check_rate_limit,
    command_context,
    defer_ephemeral,
    require_admin_or_owner,
    require_publish_permission,
)
from bot.config import settings
from bot.services.club_night_forum import (
    parse_thread_club_night,
)
from bot.services.club_night_pairing_constraints import (
    PairingConstraints,
    PairingConstraintsStore,
)
from bot.services.club_night_pairing_history import PairingHistoryStore
from bot.services.club_night_pairings import (
    PairingResult,
    format_pairings,
    generate_pairings,
)
from bot.services.club_night_signups import (
    DEFAULT_SIDE,
    SIGNUP_RETENTION_DAYS,
    FORMAT_PREF_LABELS,
    SIDE_LABELS,
    SignupStore,
    format_my_signup,
    normalize_format_pref,
)
from bot.services.discord_text import escape_public_text
from bot.services.organised_play import (
    autocomplete_event_factions,
    PLAY_STYLES,
    REPRESENTED_GAMES,
    select_game,
    signup_game,
    game_label,
    signup_options,
    signup_details,
)
from bot.services.ids import validate_guild_id
from bot.services.stadium_events import ClubNight, format_date
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT, truncate


NO_CLUB_NIGHT_MESSAGE = (
    "Run this inside a club-night thread in the organised play forum."
)
GUILD_ONLY_MESSAGE = "This command only works in a server."
STORE_ERROR_MESSAGE = "Could not save your sign-up; try again or tell an organiser."
CONSTRAINT_STORE_ERROR_MESSAGE = (
    "Could not update pairing constraints; try again or tell an organiser."
)
NOT_SIGNED_UP_MESSAGE = "Sign up first with `/club-night signup`."
PAIRINGS_POSTED_MESSAGE = "Pairings posted."
CONSTRAINTS_ERROR_MESSAGE = (
    "Could not read pairing constraints (locks, avoids, byes); refusing to pair "
    "without them. Try again, or tell an organiser if it persists."
)
PAIRINGS_HISTORY_WARNING = (
    "Pairings posted. Warning: pairing history could not be saved, so repeat "
    "avoidance may not know about tonight."
)
PAIRINGS_PREVIEW_HEADER = (
    "**Preview — not yet posted.** Reroll to reshuffle, or Post to publish to "
    "this channel."
)

_GAME_CHOICES = [
    app_commands.Choice(name=label, value=game)
    for game, label in REPRESENTED_GAMES.items()
]


_SIDE_CHOICES = [
    app_commands.Choice(name="Loyalist", value="loyalist"),
    app_commands.Choice(name="Traitor", value="traitor"),
    app_commands.Choice(name="Either", value="either"),
]

_FORMAT_CHOICES = [
    app_commands.Choice(name="Solo (no teammate; may face a team)", value="solo"),
    app_commands.Choice(name="Doubles (happy to team up)", value="doubles"),
    app_commands.Choice(name="Either (team me up if it helps)", value="either"),
]


def _signup_store(club_night: ClubNight | None = None) -> SignupStore:
    return SignupStore(settings.club_night_signup_state_path, event=club_night)


def _pairing_history_store(club_night: ClubNight | None = None) -> PairingHistoryStore:
    return PairingHistoryStore(
        settings.club_night_pairing_history_state_path, event=club_night
    )


def _constraints_store(club_night: ClubNight | None = None) -> PairingConstraintsStore:
    return PairingConstraintsStore(
        settings.club_night_pairing_constraints_state_path, event=club_night
    )


def _load_constraints(
    guild_id: str, event_date, club_night: ClubNight | None = None
) -> PairingConstraints:
    # Deliberately lets OSError propagate: locks, avoids, and forced byes are
    # authoritative organiser controls, so the command layer must refuse to
    # pair rather than silently fall back to an empty constraint set.
    return _constraints_store(club_night).get_constraints(guild_id, event_date)


async def post_and_record_pairings(
    *,
    channel,
    history_store: PairingHistoryStore,
    guild_id: str,
    event_date,
    result: PairingResult,
    signups: dict[str, dict],
    recent_opponents,
    constraints: PairingConstraints,
    channel_id,
) -> str:
    """Post a pairing result to ``channel`` and record it in history.

    Shared by the immediate ``/club-night pairings`` path and the preview's
    Post button. Returns the ephemeral confirmation to show the organiser — a
    history-write failure degrades to a warning but never retracts the post.
    """
    text = format_pairings(
        event_date,
        result,
        signups,
        recent_opponents=recent_opponents,
        locked_pairs=constraints.locked_pairs,
        avoided_pairs=constraints.avoided_pairs,
    )
    await channel.send(text, allowed_mentions=NO_MENTIONS)

    opponent_pairs = result.opponent_pairs()
    rematches = sum(1 for pair in opponent_pairs if pair in recent_opponents)
    confirmation = PAIRINGS_POSTED_MESSAGE
    try:
        # History records opponent pairs (teammates are not opponents), so a 2v1
        # or 2v2 feeds recent-opponent avoidance exactly like the 1v1s it decays.
        history_store.record_pairings(
            guild_id,
            event_date,
            [tuple(sorted(pair)) for pair in opponent_pairs],
            signups=signups,
        )
    except OSError:
        logging.exception("Could not persist club night pairing history.")
        confirmation = PAIRINGS_HISTORY_WARNING

    doubles = sum(1 for match in result.matches if not match.is_singles)
    logging.info(
        "Posted club night pairings channel=%s date=%s players=%d matches=%d "
        "doubles=%d byes=%d rematches=%d",
        channel_id,
        event_date.isoformat(),
        len(signups),
        len(result.matches),
        doubles,
        len(result.byes),
        rematches,
    )
    return confirmation


class PairingsPreviewView(discord.ui.View):
    """Ephemeral dry-run of a pairing with Reroll and Post buttons.

    Owner-scoped (non-asker presses are rejected) and disables on the 180s
    timeout, mirroring ``RerollView``. Reroll reshuffles with a fresh RNG under
    the same constraints; Post publishes the currently shown result via the
    shared :func:`post_and_record_pairings` helper.
    """

    def __init__(
        self,
        *,
        owner_user_id: int,
        guild_id: str,
        event_date,
        signups: dict[str, dict],
        recent_opponents,
        constraints: PairingConstraints,
        history_store: PairingHistoryStore,
        result: PairingResult,
    ) -> None:
        super().__init__(timeout=180)
        self._owner_user_id = owner_user_id
        self._guild_id = guild_id
        self._event_date = event_date
        self._signups = signups
        self._recent_opponents = recent_opponents
        self._constraints = constraints
        self._history_store = history_store
        self._result = result
        self._posting = False
        self._posted = False
        self.message: discord.WebhookMessage | None = None

    def _compose_content(self, prefix: str = "") -> str:
        # Reserve room for the prefix/header so the formatted pairing body can
        # never push the composed message past Discord's 2000-char ceiling; the
        # outer truncate is a belt-and-braces final cap.
        header = "\n\n".join(part for part in (prefix, PAIRINGS_PREVIEW_HEADER) if part)
        overhead = len(header) + len("\n\n")
        body = format_pairings(
            self._event_date,
            self._result,
            self._signups,
            recent_opponents=self._recent_opponents,
            locked_pairs=self._constraints.locked_pairs,
            avoided_pairs=self._constraints.avoided_pairs,
            limit=max(1, DISCORD_MESSAGE_LIMIT - overhead),
        )
        return truncate(f"{header}\n\n{body}", DISCORD_MESSAGE_LIMIT)

    def render(self) -> str:
        return self._compose_content()

    async def _reject_non_owner(self, interaction: discord.Interaction) -> bool:
        if getattr(interaction.user, "id", None) == self._owner_user_id:
            return False
        await interaction.response.send_message(
            "Only the organiser who started this preview can use it.",
            ephemeral=True,
        )
        return True

    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.secondary)
    async def reroll_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await self._reject_non_owner(interaction):
            return

        if self._posting or self._posted:
            await interaction.response.send_message(
                "These pairings are already being posted."
                if self._posting
                else "These pairings have already been posted.",
                ephemeral=True,
            )
            return

        self._result = generate_pairings(
            self._signups,
            recent_opponents=self._recent_opponents,
            locked_pairs=self._constraints.locked_pairs,
            avoided_pairs=self._constraints.avoided_pairs,
            forced_bye=self._constraints.forced_bye,
        )
        await interaction.response.edit_message(
            content=self.render(),
            view=self,
            allowed_mentions=NO_MENTIONS,
        )

    @discord.ui.button(label="Post", style=discord.ButtonStyle.primary)
    async def post_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if await self._reject_non_owner(interaction):
            return

        if self._posting or self._posted:
            await interaction.response.send_message(
                "These pairings are already being posted."
                if self._posting
                else "These pairings have already been posted.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message(
                "Could not determine the current channel.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        event = self._history_store.event
        if event is not None:
            try:
                current = _signup_store(event).get_signups(
                    self._guild_id, self._event_date
                )
                constraints = _load_constraints(self._guild_id, self._event_date, event)
            except OSError:
                await interaction.response.send_message(
                    "Could not recheck signups and constraints; try again.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            if current != self._signups or constraints != self._constraints:
                await interaction.response.send_message(
                    "Signups or constraints changed. Run `/club-night preview-pairings` again before posting.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

        self._posting = True
        try:
            confirmation = await post_and_record_pairings(
                channel=channel,
                history_store=self._history_store,
                guild_id=self._guild_id,
                event_date=self._event_date,
                result=self._result,
                signups=self._signups,
                recent_opponents=self._recent_opponents,
                constraints=self._constraints,
                channel_id=getattr(interaction, "channel_id", None),
            )
        except Exception:
            # A transient public-send failure must not leave the view wedged:
            # clear the in-flight flag so the organiser can retry.
            self._posting = False
            raise

        self._posting = False
        self._posted = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=self._compose_content(confirmation),
            view=self,
            allowed_mentions=NO_MENTIONS,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            await self.message.edit(view=self)


async def _resolve_guild_id(interaction: discord.Interaction) -> str | None:
    if interaction.guild_id is None:
        await interaction.followup.send(
            GUILD_ONLY_MESSAGE,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )
        return None

    return validate_guild_id(str(interaction.guild_id))


async def _resolve_club_night(interaction: discord.Interaction) -> ClubNight | None:
    club_night = parse_thread_club_night(
        interaction.channel,
        settings.club_night_forum_channel_id,
        today=datetime.now(UTC).date(),
    )

    if club_night is None:
        await interaction.followup.send(
            NO_CLUB_NIGHT_MESSAGE,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )

    if club_night is not None and club_night.event_date < (
        datetime.now(UTC).date() - timedelta(days=SIGNUP_RETENTION_DAYS)
    ):
        await interaction.followup.send(
            "This event is older than the 60-day signup window. "
            "You can still report and view game results in this thread.",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )
        return None
    return club_night


def _format_note(format_pref: object) -> str:
    # A gentle nudge shown after sign-up so members discover the doubles opt-in;
    # silent for the (default) solo choice so nothing changes for 1v1 regulars.
    if format_pref == "doubles":
        return " You're open to being teamed up for doubles."
    if format_pref == "either":
        return " You're happy to be teamed up if it makes the pairings work."
    return " Want doubles? Add `format:Doubles` when you sign up."


def _build_signups_text(*, club_night: ClubNight, signups: dict[str, dict]) -> str:
    count = len(signups)
    player_word = "player" if count == 1 else "players"
    lines = [
        f"**{escape_public_text(club_night.title)}** — {format_date(club_night.event_date)}",
        f"{count} {player_word} signed up:",
        "",
    ]

    for entry in sorted(signups.values(), key=lambda item: str(item.get("signed_up_at"))):
        name = escape_public_text(str(entry.get("display_name") or "Unknown"))
        label = SIDE_LABELS.get(entry.get("side"))
        game = signup_game(entry, club_night.game)
        tags = [escape_public_text(game_label(game))]
        if label and game == "heresy":
            tags.append(label)
        tags.extend(signup_details(entry))
        format_pref = normalize_format_pref(entry.get("format_pref"))
        if format_pref in ("doubles", "either"):
            tags.append(FORMAT_PREF_LABELS[format_pref])
        suffix = f" ({', '.join(tags)})" if tags else ""
        lines.append(f"- {name}{suffix}")

    return truncate("\n".join(lines), DISCORD_MESSAGE_LIMIT)


async def _signup_faction_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    event = parse_thread_club_night(
        interaction.channel,
        settings.club_night_forum_channel_id,
        today=datetime.now(UTC).date(),
    )
    selection = getattr(getattr(interaction, "namespace", None), "game", None)
    matches = autocomplete_event_factions(
        event, str(settings.summaries_dir), current, selection
    )
    return [app_commands.Choice(name=name, value=name) for name in matches[:25]]


@_club_night_group.command(
    name="signup",
    description="Sign up for this event.",
)
@app_commands.describe(
    side="The side you want to play (default: either).",
    format="Heresy only: Solo or open to doubles (keeps your last choice, or solo).",
    faction="Optional faction; omitted values keep your previous choice.",
    play_style="Optional Learning, Casual or Competitive preference.",
    rice="Bushido only: preferred rice level (usual level: 100).",
    game="Game to play; omit to use the thread title. Replaces your signup for this night.",
)
@app_commands.autocomplete(faction=_signup_faction_autocomplete)
@app_commands.choices(
    game=_GAME_CHOICES,
    side=_SIDE_CHOICES,
    format=_FORMAT_CHOICES,
    play_style=[app_commands.Choice(name=v, value=k) for k, v in PLAY_STYLES.items()],
)
async def club_night_signup(
    interaction: discord.Interaction,
    side: app_commands.Choice[str] | None = None,
    format: app_commands.Choice[str] | None = None,
    faction: str | None = None,
    play_style: app_commands.Choice[str] | None = None,
    rice: app_commands.Range[int, 1, 1000] | None = None,
    game: app_commands.Choice[str] | None = None,
) -> None:
    async with command_context(interaction, "club-night signup"):
        if not await check_rate_limit(interaction, "club-night signup"):
            return
        if not await defer_ephemeral(interaction, "club-night signup"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
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
            await interaction.followup.send(
                str(exc), ephemeral=True, allowed_mentions=NO_MENTIONS
            )
            return

        side_value = side.value if side is not None else DEFAULT_SIDE
        format_value = format.value if format is not None else None
        store = _signup_store(club_night)
        user = interaction.user
        display_name = str(getattr(user, "display_name", None) or user)

        try:
            entry = store.add_signup(
                guild_id,
                club_night.event_date,
                str(user.id),
                display_name,
                side=side_value,
                format_pref=format_value,
                details=details,
                now=datetime.now(UTC),
            )
            count = len(store.get_signups(guild_id, club_night.event_date))
        except OSError:
            logging.exception("Could not persist club night sign-up.")
            await interaction.followup.send(
                STORE_ERROR_MESSAGE,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        side_label = SIDE_LABELS.get(side_value)
        side_text = f" as {side_label}" if side_label else ""
        format_text = (
            _format_note(entry.get("format_pref")) if selected_game == "heresy" else ""
        )
        optional = signup_details(entry)
        if optional:
            format_text += " " + ", ".join(optional) + "."
        await interaction.followup.send(
            f"Signed up for {club_night.title} "
            f"({format_date(club_night.event_date)}){side_text}. "
            f"Playing {escape_public_text(game_label(selected_game))}. "
            f"{count} attending.{format_text}",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )


@_club_night_group.command(
    name="my-signup",
    description="Privately check your signup and saved options for this event.",
)
async def club_night_my_signup(interaction: discord.Interaction) -> None:
    async with command_context(interaction, "club-night my-signup"):
        if not await check_rate_limit(interaction, "club-night my-signup"):
            return
        if not await defer_ephemeral(interaction, "club-night my-signup"):
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
            logging.exception("Could not read personal club night signup.")
            await interaction.followup.send(
                "Could not read your sign-up; try again or tell an organiser.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        await interaction.followup.send(
            format_my_signup(
                event=club_night, entry=signups.get(str(interaction.user.id))
            ),
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )


@_club_night_group.command(
    name="withdraw",
    description="Withdraw your sign-up for this event.",
)
async def club_night_withdraw(interaction: discord.Interaction) -> None:
    async with command_context(interaction, "club-night withdraw"):
        if not await check_rate_limit(interaction, "club-night withdraw"):
            return
        if not await defer_ephemeral(interaction, "club-night withdraw"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        store = _signup_store(club_night)

        try:
            removed = store.remove_signup(
                guild_id,
                club_night.event_date,
                str(interaction.user.id),
            )
        except OSError:
            logging.exception("Could not persist club night withdrawal.")
            await interaction.followup.send(
                STORE_ERROR_MESSAGE,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if removed:
            message = (
                f"You're no longer signed up for {club_night.title} "
                f"({format_date(club_night.event_date)})."
            )
        else:
            message = (
                f"You weren't signed up for {club_night.title} "
                f"({format_date(club_night.event_date)})."
            )

        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )


@_club_night_group.command(
    name="signups",
    description="List who is signed up for this event.",
)
async def club_night_signups(interaction: discord.Interaction) -> None:
    async with command_context(interaction, "club-night signups"):
        if not await require_admin_or_owner(interaction, "club-night signups"):
            return
        if not await check_rate_limit(interaction, "club-night signups"):
            return
        if not await defer_ephemeral(interaction, "club-night signups"):
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
            await interaction.followup.send(
                STORE_ERROR_MESSAGE,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if not signups:
            await interaction.followup.send(
                f"No one has signed up for {club_night.title} "
                f"({format_date(club_night.event_date)}) yet.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        await interaction.followup.send(
            _build_signups_text(club_night=club_night, signups=signups),
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )


@_club_night_group.command(
    name="prefer",
    description="Name your preferred opponent for this event.",
)
@app_commands.describe(
    player="The signed-up member you would like to play.",
    clear="Clear your stored preference instead of setting one.",
)
async def club_night_prefer(
    interaction: discord.Interaction,
    player: discord.Member | None = None,
    clear: bool = False,
) -> None:
    async with command_context(interaction, "club-night prefer"):
        if not await check_rate_limit(interaction, "club-night prefer"):
            return
        if not await defer_ephemeral(interaction, "club-night prefer"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_club_night(interaction)
        if club_night is None:
            return

        store = _signup_store(club_night)
        try:
            signups = store.get_signups(guild_id, club_night.event_date)
        except OSError:
            logging.exception("Could not read club night sign-ups.")
            await interaction.followup.send(
                STORE_ERROR_MESSAGE,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        user_id = str(interaction.user.id)

        if user_id not in signups:
            await interaction.followup.send(
                NOT_SIGNED_UP_MESSAGE,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if clear:
            try:
                store.set_preference(guild_id, club_night.event_date, user_id, None)
            except OSError:
                logging.exception("Could not persist preference clear.")
                await interaction.followup.send(
                    STORE_ERROR_MESSAGE,
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            await interaction.followup.send(
                "Your opponent preference has been cleared.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if player is None:
            await interaction.followup.send(
                "Choose a player to set a preference, or use `clear:true` to clear it.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if player.id == interaction.user.id:
            await interaction.followup.send(
                "You cannot prefer yourself.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if player.bot:
            await interaction.followup.send(
                "You cannot prefer a bot.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        try:
            store.set_preference(
                guild_id,
                club_night.event_date,
                user_id,
                str(player.id),
            )
        except OSError:
            logging.exception("Could not persist opponent preference.")
            await interaction.followup.send(
                STORE_ERROR_MESSAGE,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        player_name = escape_public_text(str(getattr(player, "display_name", None) or player))
        note = (
            " They haven't signed up yet, so this only counts once they do."
            if str(player.id) not in signups
            else ""
        )
        await interaction.followup.send(
            f"Preference recorded: you'd like to play {player_name}. "
            "Pairings honour this only if they prefer you back."
            f"{note}",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )


@_club_night_group.command(
    name="pairings",
    description="Generate and post pairings for this event.",
)
async def club_night_pairings(interaction: discord.Interaction) -> None:
    async with command_context(interaction, "club-night pairings"):
        if not await require_publish_permission(interaction):
            return
        if not await check_rate_limit(interaction, "club-night pairings"):
            return
        if not await defer_ephemeral(interaction, "club-night pairings"):
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
            await interaction.followup.send(
                STORE_ERROR_MESSAGE,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        if len(signups) < 2:
            count = len(signups)
            player_word = "player" if count == 1 else "players"
            await interaction.followup.send(
                f"Only {count} {player_word} signed up — nothing to pair.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        channel = interaction.channel

        if channel is None:
            await interaction.followup.send(
                "Could not determine the current channel.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
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
            await interaction.followup.send(
                CONSTRAINTS_ERROR_MESSAGE,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        result = generate_pairings(
            signups,
            recent_opponents=recent_opponents,
            locked_pairs=constraints.locked_pairs,
            avoided_pairs=constraints.avoided_pairs,
            forced_bye=constraints.forced_bye,
        )

        confirmation = await post_and_record_pairings(
            channel=channel,
            history_store=history_store,
            guild_id=guild_id,
            event_date=club_night.event_date,
            result=result,
            signups=signups,
            recent_opponents=recent_opponents,
            constraints=constraints,
            channel_id=interaction.channel_id,
        )

        await interaction.followup.send(
            confirmation,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )
