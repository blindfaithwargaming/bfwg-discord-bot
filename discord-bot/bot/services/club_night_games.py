from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Sequence

from bot.services.ids import validate_guild_id
from bot.services.text_utils import clean_user_text


GAMES_SCHEMA_VERSION = 1
GAME_TYPE_SIDES = "sides"
MAX_PLAYERS_PER_SIDE = 4
RESULTS = ("side1", "side2", "draw")
SIDE_KEYS = ("side1", "side2")
MAX_FACTION_LENGTH = 80
MAX_DISPLAY_NAME_LENGTH = 80
MAX_MISSION_LENGTH = 120
MAX_THREAD_TITLE_LENGTH = 100
MAX_NOTES_LENGTH = 1000
POINTS_SIZE_MIN = 1
POINTS_SIZE_MAX = 100_000
CAMPAIGN_HEX_MIN = 1
CAMPAIGN_HEX_MAX = 9_999
SHORT_ID_LENGTH = 8


class GameNotFoundError(Exception):
    """No stored game matches the given game id."""


class AmbiguousGameIdError(Exception):
    """A game id prefix matches more than one stored game."""


class GameRemovalNotAllowedError(Exception):
    """The requester is neither the reporter nor allowed to remove any game."""


def validate_result(value: str) -> str:
    normalized = value.strip().lower()

    if normalized not in RESULTS:
        raise ValueError(f"result must be one of: {', '.join(RESULTS)}. Got: {value!r}")

    return normalized


def clean_faction(value: str) -> str:
    cleaned = clean_user_text(value)

    if not cleaned:
        raise ValueError("Please include a faction for every player.")

    return cleaned[:MAX_FACTION_LENGTH]


def clean_player_display_name(value: str) -> str:
    cleaned = clean_user_text(value)
    return (cleaned or "Unknown")[:MAX_DISPLAY_NAME_LENGTH]


def clean_mission(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = clean_user_text(value)
    return cleaned[:MAX_MISSION_LENGTH] or None


def clean_notes(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = clean_user_text(value)
    return cleaned[:MAX_NOTES_LENGTH] or None


def validate_points_size(value: int | None) -> int | None:
    if value is None:
        return None

    if not POINTS_SIZE_MIN <= value <= POINTS_SIZE_MAX:
        raise ValueError(
            f"Points size must be between {POINTS_SIZE_MIN} and {POINTS_SIZE_MAX}."
        )

    return value


def validate_campaign_hex(value: int | None) -> int | None:
    if value is None:
        return None

    if not CAMPAIGN_HEX_MIN <= value <= CAMPAIGN_HEX_MAX:
        raise ValueError(
            f"Campaign hex must be between {CAMPAIGN_HEX_MIN} and {CAMPAIGN_HEX_MAX}."
        )

    return value


def _player_result(side: str, result: str) -> str:
    if result == "draw":
        return "draw"

    return "win" if side == result else "loss"


class GameStore:
    """Local JSON store of played club-night games.

    The bot runs as a single instance (see design rules), so a local file is
    sufficient — no distributed lock is needed. Unlike the signup and pairing
    stores, records live in a single flat ``games`` list with per-record
    context (guild, club-night date, thread): the file is primarily an
    analytics corpus read by out-of-bot agent commands, which slice it by
    faction and player far more often than by event, and a flat list of
    versioned records tolerates new fields and game types without
    restructuring. It is never pruned — history is the point.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def _load(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"schema_version": GAMES_SCHEMA_VERSION, "games": []}
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logging.warning(
                "Game store %s is corrupt; treating as empty.", self._path
            )
            return empty
        if not isinstance(data, dict) or not isinstance(data.get("games"), list):
            logging.warning(
                "Game store %s has an unexpected shape; treating as empty.",
                self._path,
            )
            return empty

        cleaned_games = [
            entry
            for entry in data["games"]
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and isinstance(entry.get("guild_id"), str)
        ]
        if len(cleaned_games) != len(data["games"]):
            logging.warning(
                "Game store %s has malformed entries; ignoring them.", self._path
            )

        return {"schema_version": GAMES_SCHEMA_VERSION, "games": cleaned_games}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=self._path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, sort_keys=True, indent=2)
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    def add_game(
        self,
        *,
        guild_id: str,
        club_night_date: date,
        thread_id: str,
        thread_title: str,
        reported_by_user_id: str,
        result: str,
        side1: Sequence[tuple[str, str, str]],
        side2: Sequence[tuple[str, str, str]],
        points_size: int | None = None,
        mission: str | None = None,
        notes: str | None = None,
        campaign_hex: int | None = None,
        game: str = "heresy",
        rice: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record a game; sides are ``(user_id, display_name, faction)`` triples."""
        if rice is not None and (
            game != "bushido" or isinstance(rice, bool) or not 1 <= rice <= 1000
        ):
            raise ValueError(
                "Rice must be between 1 and 1000 and is only used for Bushido."
            )
        if game == "bushido" and points_size is not None:
            raise ValueError("Use rice instead of points_size for Bushido games.")
        validated_guild_id = validate_guild_id(guild_id)
        validated_result = validate_result(result)
        target_now = now or datetime.now(UTC)

        players: list[dict[str, Any]] = []
        for side_key, side_players in zip(SIDE_KEYS, (side1, side2)):
            if not 1 <= len(side_players) <= MAX_PLAYERS_PER_SIDE:
                raise ValueError(
                    f"Each side needs between 1 and {MAX_PLAYERS_PER_SIDE} players."
                )
            for user_id, display_name, faction in side_players:
                players.append(
                    {
                        "user_id": str(user_id),
                        "display_name": clean_player_display_name(display_name),
                        "faction": clean_faction(faction),
                        "side": side_key,
                        "result": _player_result(side_key, validated_result),
                    }
                )

        user_ids = [player["user_id"] for player in players]
        if len(set(user_ids)) != len(user_ids):
            raise ValueError("Each player can only appear once in a game.")

        entry = {
            "id": uuid.uuid4().hex,
            "guild_id": validated_guild_id,
            "club_night_date": club_night_date.isoformat(),
            "thread_id": str(thread_id),
            "thread_title": clean_user_text(thread_title)[:MAX_THREAD_TITLE_LENGTH],
            "reported_by_user_id": str(reported_by_user_id),
            "reported_at": target_now.isoformat(),
            "game_type": GAME_TYPE_SIDES,
            "result": validated_result,
            "players": players,
            "points_size": validate_points_size(points_size),
            "game": game,
            "rice": rice,
            "mission": clean_mission(mission),
            "notes": clean_notes(notes),
            "campaign_hex": validate_campaign_hex(campaign_hex),
        }

        data = self._load()
        data["games"].append(entry)
        self._save(data)
        return entry

    def remove_game(
        self,
        guild_id: str,
        game_id: str,
        *,
        requester_user_id: str,
        allow_any: bool = False,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove a game by full id or unique prefix; returns the removed entry."""
        validated_guild_id = validate_guild_id(guild_id)
        normalized_id = game_id.strip().lower()

        if len(normalized_id) < SHORT_ID_LENGTH:
            raise ValueError(
                f"Game ref must be at least {SHORT_ID_LENGTH} characters."
            )

        data = self._load()
        matches = [
            entry
            for entry in data["games"]
            if entry["guild_id"] == validated_guild_id
            and entry["id"].startswith(normalized_id)
            and (thread_id is None or entry.get("thread_id") == str(thread_id))
        ]

        if not matches:
            raise GameNotFoundError(f"No game found with ref {normalized_id!r}.")
        if len(matches) > 1:
            raise AmbiguousGameIdError(
                f"Game ref {normalized_id!r} matches more than one game."
            )

        entry = matches[0]
        if not allow_any and entry.get("reported_by_user_id") != str(requester_user_id):
            raise GameRemovalNotAllowedError(
                "Only the reporter or an admin can remove this game."
            )

        data["games"].remove(entry)
        self._save(data)
        return entry

    def get_games_for_thread(
        self, guild_id: str, thread_id: str
    ) -> list[dict[str, Any]]:
        validated_guild_id = validate_guild_id(guild_id)

        return [
            dict(entry)
            for entry in self._load()["games"]
            if entry["guild_id"] == validated_guild_id
            and entry.get("thread_id") == str(thread_id)
        ]
