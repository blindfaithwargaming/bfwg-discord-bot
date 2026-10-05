import logging
from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands

from bot.commands.club_night_group import _club_night_group
from bot.commands.club_night_pairings import _GAME_CHOICES
from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import (
    check_rate_limit,
    command_context,
    defer_ephemeral,
    is_admin_or_owner,
)
from bot.config import settings
from bot.services import state_sync
from bot.services.club_night_forum import parse_thread_club_night
from bot.services.organised_play import (
    autocomplete_event_factions,
    select_game,
    game_label,
)
from bot.services.club_night_games import (
    CAMPAIGN_HEX_MAX,
    CAMPAIGN_HEX_MIN,
    POINTS_SIZE_MAX,
    POINTS_SIZE_MIN,
    SHORT_ID_LENGTH,
    AmbiguousGameIdError,
    GameNotFoundError,
    GameRemovalNotAllowedError,
    GameStore,
)
from bot.services.discord_text import escape_public_text
from bot.services.ids import validate_guild_id
from bot.services.mission_generator import autocomplete_mission_title
from bot.services.stadium_events import ClubNight, format_date
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT, truncate


GUILD_ONLY_MESSAGE = "This command only works in a server."
NOT_CLUB_NIGHT_THREAD_MESSAGE = (
    "Run this inside a club-night thread in the organised play forum."
)
GAME_STORE_ERROR_MESSAGE = "Could not save the game; try again or tell an organiser."
REMOVE_STORE_ERROR_MESSAGE = (
    "Could not remove the game; try again or tell an organiser."
)
GAMES_READ_ERROR_MESSAGE = (
    "Could not read the recorded games; try again or tell an organiser."
)
GAME_NOT_FOUND_MESSAGE = "No game found with that game ref."
AMBIGUOUS_GAME_MESSAGE = (
    "That game ref matches more than one game; use the full game id from your "
    "confirmation message."
)
REMOVAL_NOT_ALLOWED_MESSAGE = (
    "Only the person who reported a game (or an admin) can remove it."
)
NO_GAMES_MESSAGE = "No games recorded for this club night yet."

_RESULT_LABELS = {"side1": "side 1 won", "side2": "side 2 won", "draw": "draw"}

_RESULT_CHOICES = [
    app_commands.Choice(name="Side 1 won", value="side1"),
    app_commands.Choice(name="Side 2 won", value="side2"),
    app_commands.Choice(name="Draw", value="draw"),
]


def _game_store() -> GameStore:
    return GameStore(settings.club_night_games_state_path)


async def _faction_autocomplete(
    interaction: discord.Interaction,
    current: str,
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
    return [app_commands.Choice(name=m, value=m) for m in matches]


async def _mission_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    event = parse_thread_club_night(
        interaction.channel,
        settings.club_night_forum_channel_id,
        today=datetime.now(UTC).date(),
    )
    if event is None:
        return []
    selection = getattr(getattr(interaction, "namespace", None), "game", None)
    try:
        if select_game(event, selection) != "heresy":
            return []
    except ValueError:
        return []
    matches = autocomplete_mission_title(str(settings.summaries_dir), current)
    return [app_commands.Choice(name=m, value=m) for m in matches]


async def _resolve_guild_id(interaction: discord.Interaction) -> str | None:
    if interaction.guild_id is None:
        await interaction.followup.send(
            GUILD_ONLY_MESSAGE,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )
        return None

    return validate_guild_id(str(interaction.guild_id))


async def _resolve_thread_club_night(
    interaction: discord.Interaction,
) -> ClubNight | None:
    club_night = parse_thread_club_night(
        interaction.channel,
        settings.club_night_forum_channel_id,
        today=datetime.now(UTC).date(),
    )

    if club_night is None:
        await interaction.followup.send(
            NOT_CLUB_NIGHT_THREAD_MESSAGE,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )

    return club_night


def _collect_side(
    slots: list[tuple[Any, str | None]],
    side_label: str,
    *,
    faction_required: bool,
) -> list[tuple[str, str, str]]:
    """Turn optional (player, faction) slots into ``(user_id, name, faction)`` triples."""
    players: list[tuple[str, str, str]] = []

    for index, (player, faction) in enumerate(slots, start=1):
        if player is None and faction is None:
            continue
        if player is None or (faction_required and faction is None):
            raise ValueError(
                f"{side_label} player {index} needs both a player and a faction."
            )
        if getattr(player, "bot", False):
            raise ValueError("Bots cannot play games.")

        display_name = str(getattr(player, "display_name", None) or player)
        players.append((str(player.id), display_name, faction or "Not specified"))

    return players


def _render_side_players(entry: dict[str, Any], side_key: str) -> str:
    return ", ".join(
        f"{escape_public_text(str(player.get('display_name') or 'Unknown'))} "
        f"({escape_public_text(str(player.get('faction') or 'Unknown'))})"
        for player in entry.get("players", [])
        if player.get("side") == side_key
    )


def _build_game_confirmation_text(*, entry: dict[str, Any]) -> str:
    result = str(entry.get("result", ""))
    lines = [f"**Game reported — {_RESULT_LABELS.get(result, result)}**"]

    for side_key, label in (("side1", "Side 1"), ("side2", "Side 2")):
        marker = " (winner)" if side_key == result else ""
        lines.append(f"{label}{marker}: {_render_side_players(entry, side_key)}")

    details = [
        f"Game: {escape_public_text(game_label(str(entry.get('game') or 'heresy')))}"
    ]
    if entry.get("rice"):
        details.append(f"{entry['rice']} rice")
    if entry.get("points_size"):
        details.append(f"{entry['points_size']} points")
    if entry.get("mission"):
        details.append(f"Mission: {escape_public_text(str(entry['mission']))}")
    if entry.get("campaign_hex"):
        details.append(f"Campaign hex: {entry['campaign_hex']}")
    if details:
        lines.append(" — ".join(details))
    if entry.get("notes"):
        lines.append(f"Notes: {escape_public_text(str(entry['notes']))}")

    short_ref = str(entry.get("id", ""))[:SHORT_ID_LENGTH]
    lines.append(
        f"Game ref: `{short_ref}` — reporter or an admin can remove this with "
        "`/club-night remove-game`."
    )

    return truncate("\n".join(lines), DISCORD_MESSAGE_LIMIT)


def _build_games_list_text(
    *,
    club_night: ClubNight,
    games: list[dict[str, Any]],
) -> str:
    if not games:
        return NO_GAMES_MESSAGE

    count = len(games)
    game_word = "game" if count == 1 else "games"
    lines = [
        f"**{escape_public_text(club_night.title)}** — {format_date(club_night.event_date)}",
        f"{count} {game_word} recorded:",
        "",
    ]

    for entry in games:
        result = str(entry.get("result", ""))
        short_ref = str(entry.get("id", ""))[:SHORT_ID_LENGTH]
        label = escape_public_text(game_label(str(entry.get("game") or "heresy")))
        lines.append(
            f"- `{short_ref}` {label} — {_RESULT_LABELS.get(result, result)}: "
            f"{_render_side_players(entry, 'side1')} vs "
            f"{_render_side_players(entry, 'side2')}"
        )

    return truncate("\n".join(lines), DISCORD_MESSAGE_LIMIT)


@_club_night_group.command(
    name="report-game",
    description="Record a played game in this club-night thread.",
)
@app_commands.describe(
    result="Who won the game.",
    side1_player1="Side 1, player 1.",
    side1_faction1="Side 1, player 1's faction.",
    side2_player1="Side 2, player 1.",
    side2_faction1="Side 2, player 1's faction.",
    side1_player2="Side 1, player 2 (team games).",
    side1_faction2="Side 1, player 2's faction.",
    side1_player3="Side 1, player 3 (team games).",
    side1_faction3="Side 1, player 3's faction.",
    side1_player4="Side 1, player 4 (team games).",
    side1_faction4="Side 1, player 4's faction.",
    side2_player2="Side 2, player 2 (team games).",
    side2_faction2="Side 2, player 2's faction.",
    side2_player3="Side 2, player 3 (team games).",
    side2_faction3="Side 2, player 3's faction.",
    side2_player4="Side 2, player 4 (team games).",
    side2_faction4="Side 2, player 4's faction.",
    points_size="Points the game was played at (e.g. 3000).",
    mission="The mission played.",
    notes="A short note about the game.",
    rice="Bushido only: rice level the game was played at.",
    game="Game played; omit to use the thread title.",
    campaign_hex="The campaign map hex the game was fought over.",
)
@app_commands.choices(result=_RESULT_CHOICES, game=_GAME_CHOICES)
@app_commands.autocomplete(
    side1_faction1=_faction_autocomplete,
    side1_faction2=_faction_autocomplete,
    side1_faction3=_faction_autocomplete,
    side1_faction4=_faction_autocomplete,
    side2_faction1=_faction_autocomplete,
    side2_faction2=_faction_autocomplete,
    side2_faction3=_faction_autocomplete,
    side2_faction4=_faction_autocomplete,
    mission=_mission_autocomplete,
)
async def club_night_report_game(
    interaction: discord.Interaction,
    result: app_commands.Choice[str],
    side1_player1: discord.Member,
    side2_player1: discord.Member,
    side1_faction1: str | None = None,
    side2_faction1: str | None = None,
    side1_player2: discord.Member | None = None,
    side1_faction2: str | None = None,
    side1_player3: discord.Member | None = None,
    side1_faction3: str | None = None,
    side1_player4: discord.Member | None = None,
    side1_faction4: str | None = None,
    side2_player2: discord.Member | None = None,
    side2_faction2: str | None = None,
    side2_player3: discord.Member | None = None,
    side2_faction3: str | None = None,
    side2_player4: discord.Member | None = None,
    side2_faction4: str | None = None,
    points_size: (
        app_commands.Range[int, POINTS_SIZE_MIN, POINTS_SIZE_MAX] | None
    ) = None,
    mission: str | None = None,
    notes: str | None = None,
    campaign_hex: (
        app_commands.Range[int, CAMPAIGN_HEX_MIN, CAMPAIGN_HEX_MAX] | None
    ) = None,
    rice: app_commands.Range[int, 1, 1000] | None = None,
    game: app_commands.Choice[str] | None = None,
) -> None:
    async with command_context(interaction, "club-night report-game"):
        if not await check_rate_limit(interaction, "club-night report-game"):
            return
        if not await defer_ephemeral(interaction, "club-night report-game"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_thread_club_night(interaction)
        if club_night is None:
            return

        try:
            selected_game = select_game(club_night, game.value if game else None)
            side1 = _collect_side(
                [
                    (side1_player1, side1_faction1),
                    (side1_player2, side1_faction2),
                    (side1_player3, side1_faction3),
                    (side1_player4, side1_faction4),
                ],
                "Side 1",
                faction_required=selected_game == "heresy",
            )
            side2 = _collect_side(
                [
                    (side2_player1, side2_faction1),
                    (side2_player2, side2_faction2),
                    (side2_player3, side2_faction3),
                    (side2_player4, side2_faction4),
                ],
                "Side 2",
                faction_required=selected_game == "heresy",
            )
        except ValueError as exc:
            await interaction.followup.send(
                str(exc),
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        store = _game_store()
        try:
            entry = store.add_game(
                guild_id=guild_id,
                club_night_date=club_night.event_date,
                thread_id=str(interaction.channel_id),
                thread_title=club_night.title,
                reported_by_user_id=str(interaction.user.id),
                result=result.value,
                side1=side1,
                side2=side2,
                points_size=points_size,
                mission=mission,
                notes=notes,
                campaign_hex=campaign_hex,
                game=selected_game,
                rice=rice,
            )
        except ValueError as exc:
            await interaction.followup.send(
                str(exc),
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        except OSError:
            logging.exception("Could not persist club night game.")
            await interaction.followup.send(
                GAME_STORE_ERROR_MESSAGE,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        state_sync.schedule_upload(settings.club_night_games_state_path, settings)

        public_text = _build_game_confirmation_text(entry=entry)
        try:
            await interaction.channel.send(public_text, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            # The record is already saved; never retract it because the
            # public summary could not be posted.
            logging.exception("Could not post the game summary to the thread.")
            await interaction.followup.send(
                f"Game recorded (game id `{entry['id']}`), but the summary could "
                "not be posted to this thread.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        logging.info(
            "Recorded club night game guild=%s date=%s thread=%s game=%s players=%d",
            guild_id,
            club_night.event_date.isoformat(),
            interaction.channel_id,
            entry["id"],
            len(entry["players"]),
        )
        await interaction.followup.send(
            f"Game recorded and posted to this thread. Game id: `{entry['id']}`.",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )


@_club_night_group.command(
    name="remove-game",
    description="Remove a game you reported (admins can remove any game).",
)
@app_commands.describe(
    game_id="The game ref from the confirmation post (at least 8 characters).",
)
async def club_night_remove_game(
    interaction: discord.Interaction,
    game_id: str,
) -> None:
    async with command_context(interaction, "club-night remove-game"):
        if not await check_rate_limit(interaction, "club-night remove-game"):
            return
        if not await defer_ephemeral(interaction, "club-night remove-game"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return

        club_night = await _resolve_thread_club_night(interaction)
        if club_night is None:
            return

        store = _game_store()
        try:
            entry = store.remove_game(
                guild_id,
                game_id,
                requester_user_id=str(interaction.user.id),
                allow_any=is_admin_or_owner(interaction),
                thread_id=str(interaction.channel_id),
            )
        except GameNotFoundError:
            message = GAME_NOT_FOUND_MESSAGE
        except AmbiguousGameIdError:
            message = AMBIGUOUS_GAME_MESSAGE
        except GameRemovalNotAllowedError:
            message = REMOVAL_NOT_ALLOWED_MESSAGE
        except ValueError as exc:
            message = str(exc)
        except OSError:
            logging.exception("Could not remove club night game.")
            message = REMOVE_STORE_ERROR_MESSAGE
        else:
            state_sync.schedule_upload(settings.club_night_games_state_path, settings)
            logging.info(
                "Removed club night game guild=%s game=%s by user=%s",
                guild_id,
                entry["id"],
                interaction.user.id,
            )
            message = truncate(
                f"Removed game `{entry['id'][:SHORT_ID_LENGTH]}`: "
                f"{_render_side_players(entry, 'side1')} vs "
                f"{_render_side_players(entry, 'side2')}.",
                DISCORD_MESSAGE_LIMIT,
            )

        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )


@_club_night_group.command(
    name="games",
    description="List the games recorded in this club-night thread.",
)
async def club_night_games(interaction: discord.Interaction) -> None:
    async with command_context(interaction, "club-night games"):
        if not await check_rate_limit(interaction, "club-night games"):
            return
        if not await defer_ephemeral(interaction, "club-night games"):
            return

        guild_id = await _resolve_guild_id(interaction)
        if guild_id is None:
            return
        club_night = await _resolve_thread_club_night(interaction)
        if club_night is None:
            return

        try:
            games = _game_store().get_games_for_thread(
                guild_id, str(interaction.channel_id)
            )
        except OSError:
            logging.exception("Could not read club night games.")
            await interaction.followup.send(
                GAMES_READ_ERROR_MESSAGE,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        await interaction.followup.send(
            _build_games_list_text(club_night=club_night, games=games),
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )
