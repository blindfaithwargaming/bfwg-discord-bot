from datetime import date
from typing import Any
from urllib.parse import quote, unquote

from bot.services.discord_text import escape_public_text
from bot.services.faction_index import autocomplete_faction
from bot.services.stadium_events import ClubNight
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT, truncate

# Matches the committee draft's committee/roles/game-rep.md. Discord-role
# discovery is deliberately deferred until the club uses those roles.
REPRESENTED_GAMES = {
    "heresy": "Horus Heresy",
    "40k-3rd": "Warhammer 40,000 3rd Edition",
    "bushido": "Bushido",
    "old-world": "Warhammer: The Old World",
}
GAME_ALIASES = {
    "horus heresy": "heresy",
    "warhammer 40,000 3rd edition": "40k-3rd",
    "warhammer 40k 3rd edition": "40k-3rd",
    "40k 3rd edition": "40k-3rd",
    "3rd edition": "40k-3rd",
    "3rd ed": "40k-3rd",
    "warhammer: the old world": "old-world",
    "warhammer the old world": "old-world",
    "the old world": "old-world",
    "old world": "old-world",
}


def normalize_game(value: str) -> str:
    normalized = " ".join(value.casefold().split())
    return GAME_ALIASES.get(normalized, normalized)


def select_game(event: ClubNight, selection: str | None = None) -> str:
    if selection is None:
        return normalize_game(event.game)
    game = normalize_game(selection)
    if game not in REPRESENTED_GAMES:
        raise ValueError("Choose a game from the club's represented games.")
    return game


def game_label(game: str) -> str:
    return REPRESENTED_GAMES.get(normalize_game(game), game.title())


def signup_game(entry: dict, default: str = "heresy") -> str:
    value = entry.get("game")
    return (
        normalize_game(value) if isinstance(value, str) and value.strip() else default
    )


def group_signups(
    signups: dict[str, dict], default: str = "heresy"
) -> dict[str, dict[str, dict]]:
    groups: dict[str, dict[str, dict]] = {}
    for user_id, entry in signups.items():
        groups.setdefault(signup_game(entry, default), {})[user_id] = entry
    return groups


# Official faction navigation: https://gctstudios.com/collections (2026-10-05).
BUSHIDO_FACTIONS = (
    "Cult of Yurei",
    "Horse Lords of Ataka",
    "Ito Clan",
    "Jung Pirates",
    "Kinshi Temple",
    "Minimoto Clan",
    "Prefecture of Ryu",
    "Shadow Wind Clan",
    "Shiho Clan",
    "Silvermoon Trade Syndicate",
    "Temple of Ro-Kan",
    "The Awoken",
    "The Descension",
    "The Imperial Court",
    "The Savage Wave",
)
PLAY_STYLES = {"learning": "Learning", "casual": "Casual", "competitive": "Competitive"}


def event_key(event_date: date, event: ClubNight | None) -> str:
    if event is None or event.thread_id is None:
        return event_date.isoformat()
    return f"{event_date.isoformat()}|{quote(event.game, safe='')}|{event.thread_id}"


def key_date(key: str) -> date:
    return date.fromisoformat(key.split("|", 1)[0])


def key_game(key: str) -> str:
    parts = key.split("|")
    return unquote(parts[1]) if len(parts) == 3 else "heresy"


def adopt_legacy_event(data: dict, event: ClubNight | None) -> dict:
    # Old date-only records belong to Heresy. Claim them once, on the first
    # write in that date's Heresy thread; never expose them to another game.
    if event is not None and event.game == "heresy" and event.thread_id is not None:
        old_key = event.event_date.isoformat()
        new_key = event_key(event.event_date, event)
        for events in data.values():
            if old_key in events and new_key not in events:
                events[new_key] = events.pop(old_key)
    return data


def signup_options(
    *,
    game: str,
    faction: str | None,
    play_style: str | None,
    rice: int | None,
    side: str | None,
    format_pref: str | None,
) -> dict[str, Any]:
    if game != "heresy" and (side is not None or format_pref is not None):
        raise ValueError("Side and doubles format are only used for Heresy events.")
    if game != "bushido" and rice is not None:
        raise ValueError("Rice level is only used for Bushido events.")
    if rice is not None and (isinstance(rice, bool) or not 1 <= rice <= 1000):
        raise ValueError("Rice level must be between 1 and 1000.")
    if play_style is not None and play_style not in PLAY_STYLES:
        raise ValueError("Choose Learning, Casual or Competitive.")
    if faction is not None:
        faction = faction.strip()
        if not faction or len(faction) > 100:
            raise ValueError("Faction must be between 1 and 100 characters.")
        if game == "bushido":
            names = {name.casefold(): name for name in BUSHIDO_FACTIONS}
            if faction.casefold() not in names:
                raise ValueError("Choose a Bushido faction from the suggestions.")
            faction = names[faction.casefold()]
    return {
        k: v
        for k, v in {
            "game": game,
            "faction": faction,
            "play_style": play_style,
            "rice": rice,
        }.items()
        if v is not None
    }


def signup_details(entry: dict) -> list[str]:
    details = []
    if entry.get("faction"):
        details.append(escape_public_text(str(entry["faction"])))
    if isinstance(entry.get("play_style"), str) and entry["play_style"] in PLAY_STYLES:
        details.append(PLAY_STYLES[entry["play_style"]])
    if entry.get("rice") is not None:
        details.append(f"{entry['rice']} rice")
    return details


def build_signup_instructions(*, event: ClubNight) -> str:
    lines = [
        f"**How to sign up — {escape_public_text(event.title)}**",
        "Follow the ticket/payment instructions in the event's opening post. "
        "The bot does not buy or verify tickets.",
        "Run `/club-night signup` in this thread. No options are required.",
        "Use `/club-night my-signup` to privately check your signup and saved options.",
        f"Leave `game` blank to play {escape_public_text(game_label(event.game))}, as named in the thread. "
        "Or choose `game`: " + ", ".join(REPRESENTED_GAMES.values()) + ". "
        "You can sign up for one game per night. Signing up again replaces your signup for that date, "
        "including one in another event thread. Leaving `game` blank always resets it to this thread's game.",
    ]
    lines.append(
        "For Bushido, add optional `faction`, `play_style` (Learning, Casual or Competitive), "
        "and `rice` if you want a different level from the usual 100 rice. "
        "These details help you and the organiser arrange your game; "
        "they do not prevent you being paired."
    )
    lines.append(
        "For Heresy, `side` and `format` (Solo, Doubles or Either) are optional. "
        "Other games use singles. `faction` and `play_style` are optional for every game."
    )
    lines.extend(
        [
            "Run signup again to update your optional details. Use `/club-night withdraw` to leave.",
            "Organisers post pairings with `/club-night pairings` or `/club-night preview-pairings`. "
            "Pairings stay within each selected game and try to avoid recent opponents; an odd player may receive a bye.",
            "After playing, use `/club-night report-game` here to record the players and result. "
            "Set `game` when reporting a different game from the thread title. "
            "Use `/club-night games` to see this event's results.",
            "All `/club-night` commands must be used inside an organised play event thread.",
        ]
    )
    return truncate("\n\n".join(lines), DISCORD_MESSAGE_LIMIT)


def autocomplete_event_factions(
    event: ClubNight | None,
    summaries_dir: str,
    current: str,
    selection: str | None = None,
) -> list[str]:
    if event is None:
        return []
    try:
        game = select_game(event, selection)
    except ValueError:
        return []
    if game == "bushido":
        return [
            name for name in BUSHIDO_FACTIONS if current.casefold() in name.casefold()
        ]
    if game == "heresy":
        return autocomplete_faction(summaries_dir, current)
    return []
