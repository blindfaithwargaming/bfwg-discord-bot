from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.services.ids import validate_guild_id
from bot.services.stadium_events import ClubNight, format_date
from bot.services.discord_text import escape_public_text
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT, truncate
from bot.services.organised_play import (
    adopt_legacy_event,
    event_key,
    key_date,
    signup_game,
    game_label,
    PLAY_STYLES,
)

SIGNUP_RETENTION_DAYS = 60
SIDES = ("loyalist", "traitor", "either")
DEFAULT_SIDE = "either"
SIDE_LABELS = {"loyalist": "Loyalist", "traitor": "Traitor"}

# Whether a member is willing to be auto-teamed into a doubles (2-player) side
# when pairings are generated. ``solo`` never gets a teammate (but may face a team); ``doubles``
# is teamed eagerly; ``either`` is teamed only when it improves the night (e.g.
# to turn an odd-one-out bye into a 2v1). ``solo`` is the default so the feature
# is strictly opt-in and a plain sign-up keeps today's 1v1 behaviour.
FORMAT_PREFS = ("solo", "doubles", "either")
DEFAULT_FORMAT_PREF = "solo"
FORMAT_PREF_LABELS = {
    "solo": "Solo (no teammate)",
    "doubles": "Doubles (team up)",
    "either": "Either",
}


def validate_side(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"side must be one of: {', '.join(SIDES)}. Got: {value!r}")
    normalized = value.strip().lower()

    if normalized not in SIDES:
        raise ValueError(f"side must be one of: {', '.join(SIDES)}. Got: {value!r}")

    return normalized


def normalize_side(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in SIDES:
        return value.strip().lower()
    return DEFAULT_SIDE


def validate_format_pref(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(
            f"format must be one of: {', '.join(FORMAT_PREFS)}. Got: {value!r}"
        )
    normalized = value.strip().lower()

    if normalized not in FORMAT_PREFS:
        raise ValueError(
            f"format must be one of: {', '.join(FORMAT_PREFS)}. Got: {value!r}"
        )

    return normalized


def normalize_format_pref(value: Any) -> str:
    """Coerce a stored/raw ``format_pref`` to a valid value, defaulting safely.

    Records written before this field existed (and any malformed value) read
    back as :data:`DEFAULT_FORMAT_PREF`, so the store and pairing generator never
    have to special-case legacy sign-ups.
    """
    if isinstance(value, str) and value.strip().lower() in FORMAT_PREFS:
        return value.strip().lower()
    return DEFAULT_FORMAT_PREF


def normalize_preferred_user_id(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def format_my_signup(*, event: ClubNight, entry: dict[str, Any] | None) -> str:
    event_text = f"{escape_public_text(event.title)} ({format_date(event.event_date)})"
    if entry is None:
        return truncate(
            f"You're not signed up in this event thread: {event_text}. "
            "If you signed up in another thread for this date, check there. "
            "Use `/club-night signup` here to sign up for this event.",
            DISCORD_MESSAGE_LIMIT,
        )

    game = signup_game(entry, event.game)
    lines = [
        f"**You're signed up for {event_text}.**",
        f"Game: {game_label(game)}",
        f"Faction: {escape_public_text(str(entry.get('faction') or 'Not selected'))}",
        f"Play style: {PLAY_STYLES.get(entry.get('play_style'), 'Not selected')}",
    ]
    if game == "heresy":
        lines.extend([
            f"Side: {SIDE_LABELS.get(normalize_side(entry.get('side')), 'Either')}",
            f"Format: {FORMAT_PREF_LABELS[normalize_format_pref(entry.get('format_pref'))]}",
        ])
    if game == "bushido":
        rice = entry.get("rice")
        lines.append(f"Rice: {escape_public_text(str(rice)) if rice is not None else 'Not selected'}")
    preferred = normalize_preferred_user_id(entry.get("preferred_user_id"))
    opponent = f"<@{preferred}>" if preferred and preferred.isdecimal() else "None"
    lines.append(f"Preferred opponent: {opponent}")
    if opponent != "None":
        lines.append("Opponent preferences apply only when mutual and for the same game.")
    lines.append("Use `/club-night signup` to update your options.")
    return truncate("\n".join(lines), DISCORD_MESSAGE_LIMIT)


class SignupStore:
    """Local JSON store of club-night sign-ups.

    The bot runs as a single instance (see design rules), so a local file is
    sufficient — no distributed lock is needed. State is keyed by ``guild_id``
    then date/game/thread key (legacy keys are ISO dates) then ``user_id`` and persisted as JSON, mirroring
    ``MarkerStore`` in ``club_night_scheduler.py``.
    """

    def __init__(
        self, path: str | os.PathLike[str], *, event: ClubNight | None = None
    ) -> None:
        self._path = Path(path)
        self.event = event

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logging.warning(
                "Signup store %s is corrupt; treating as empty.", self._path
            )
            return {}
        if not isinstance(data, dict):
            return {}

        cleaned: dict[str, dict[str, Any]] = {}
        malformed = False

        for guild_id, events in data.items():
            if not isinstance(events, dict):
                malformed = True
                continue

            cleaned_events: dict[str, dict[str, Any]] = {}
            for event_key, signups in events.items():
                if not isinstance(signups, dict):
                    malformed = True
                    continue

                cleaned_signups = {
                    user_id: entry
                    for user_id, entry in signups.items()
                    if isinstance(entry, dict)
                }
                if len(cleaned_signups) != len(signups):
                    malformed = True
                if cleaned_signups:
                    cleaned_events[event_key] = cleaned_signups

            if cleaned_events:
                cleaned[guild_id] = cleaned_events

        if malformed:
            logging.warning(
                "Signup store %s has malformed entries; ignoring them.",
                self._path,
            )
        return adopt_legacy_event(cleaned, self.event)

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
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

    def add_signup(
        self,
        guild_id: str,
        event_date: date,
        user_id: str,
        display_name: str,
        *,
        side: str = DEFAULT_SIDE,
        format_pref: str | None = None,
        details: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        validated_guild_id = validate_guild_id(guild_id)
        target_now = now or datetime.now(UTC)
        data = self._load()
        signups = data.setdefault(validated_guild_id, {}).setdefault(
            event_key(event_date, self.event), {}
        )
        existing = dict(signups.get(user_id) or {})
        next_game = signup_game(
            details or {}, self.event.game if self.event else "heresy"
        )
        if (
            signup_game(existing, self.event.game if self.event else "heresy")
            != next_game
        ):
            for field in (
                "faction",
                "play_style",
                "rice",
                "preferred_user_id",
                "format_pref",
                "side",
            ):
                existing.pop(field, None)
        entry = {
            **existing,
            "display_name": display_name,
            "signed_up_at": target_now.isoformat(),
            "side": validate_side(side),
            # Re-signing up (e.g. to change side) must not drop a stored preference.
            "preferred_user_id": normalize_preferred_user_id(
                existing.get("preferred_user_id")
            ),
            # ``format_pref=None`` means "leave my format choice as it was" so a
            # member re-signing to change side keeps their doubles opt-in.
            "format_pref": (
                validate_format_pref(format_pref)
                if format_pref is not None
                else normalize_format_pref(existing.get("format_pref"))
            ),
        }
        if self.event is not None:
            entry["game"] = next_game
        if details:
            entry.update(
                {
                    k: v
                    for k, v in details.items()
                    if k in {"game", "faction", "play_style", "rice"}
                }
            )
        if self.event is not None:
            for other_key, other_signups in data[validated_guild_id].items():
                if other_key == event_key(event_date, self.event):
                    continue
                try:
                    same_night = key_date(other_key) == event_date
                except ValueError:
                    continue
                if same_night:
                    other_signups.pop(user_id, None)
        signups[user_id] = entry
        self._prune(data, target_now)
        self._save(data)
        return entry

    def remove_signup(
        self,
        guild_id: str,
        event_date: date,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        validated_guild_id = validate_guild_id(guild_id)
        data = self._load()
        signups = data.get(validated_guild_id, {}).get(
            event_key(event_date, self.event), {}
        )
        removed = signups.pop(user_id, None) is not None
        self._prune(data, now or datetime.now(UTC))
        self._save(data)
        return removed

    def set_side(
        self,
        guild_id: str,
        event_date: date,
        user_id: str,
        side: str,
        *,
        now: datetime | None = None,
    ) -> None:
        validated_guild_id = validate_guild_id(guild_id)
        data = self._load()
        signups = data.get(validated_guild_id, {}).get(
            event_key(event_date, self.event), {}
        )
        entry = signups.get(user_id)

        if not isinstance(entry, dict):
            raise KeyError(
                f"User {user_id} is not signed up for {event_date.isoformat()}."
            )

        if signup_game(entry, self.event.game if self.event else "heresy") != "heresy":
            raise ValueError(
                "Side and doubles format are only used for Heresy signups."
            )

        entry["side"] = validate_side(side)
        self._prune(data, now or datetime.now(UTC))
        self._save(data)

    def set_format(
        self,
        guild_id: str,
        event_date: date,
        user_id: str,
        format_pref: str,
        *,
        now: datetime | None = None,
    ) -> None:
        validated_guild_id = validate_guild_id(guild_id)
        data = self._load()
        signups = data.get(validated_guild_id, {}).get(
            event_key(event_date, self.event), {}
        )
        entry = signups.get(user_id)

        if not isinstance(entry, dict):
            raise KeyError(
                f"User {user_id} is not signed up for {event_date.isoformat()}."
            )

        if signup_game(entry, self.event.game if self.event else "heresy") != "heresy":
            raise ValueError(
                "Side and doubles format are only used for Heresy signups."
            )

        entry["format_pref"] = validate_format_pref(format_pref)
        self._prune(data, now or datetime.now(UTC))
        self._save(data)

    def clear_event(
        self,
        guild_id: str,
        event_date: date,
        *,
        now: datetime | None = None,
    ) -> int:
        validated_guild_id = validate_guild_id(guild_id)
        data = self._load()
        events = data.get(validated_guild_id, {})
        removed = events.pop(event_key(event_date, self.event), {})
        count = len(removed) if isinstance(removed, dict) else 0
        self._prune(data, now or datetime.now(UTC))
        self._save(data)
        return count

    def set_preference(
        self,
        guild_id: str,
        event_date: date,
        user_id: str,
        preferred_user_id: str | None,
        *,
        now: datetime | None = None,
    ) -> None:
        validated_guild_id = validate_guild_id(guild_id)
        data = self._load()
        signups = data.get(validated_guild_id, {}).get(
            event_key(event_date, self.event), {}
        )
        entry = signups.get(user_id)

        if not isinstance(entry, dict):
            raise KeyError(
                f"User {user_id} is not signed up for {event_date.isoformat()}."
            )

        entry["preferred_user_id"] = preferred_user_id
        self._prune(data, now or datetime.now(UTC))
        self._save(data)

    def get_signups(self, guild_id: str, event_date: date) -> dict[str, dict[str, Any]]:
        validated_guild_id = validate_guild_id(guild_id)
        signups = (
            self._load()
            .get(validated_guild_id, {})
            .get(event_key(event_date, self.event), {})
        )

        result: dict[str, dict[str, Any]] = {}
        for user_id, entry in signups.items():
            if not isinstance(entry, dict):
                continue
            normalized = dict(entry)
            if self.event is not None:
                normalized["game"] = signup_game(entry, self.event.game)
            normalized["side"] = normalize_side(normalized.get("side"))
            normalized["format_pref"] = normalize_format_pref(
                normalized.get("format_pref")
            )
            normalized["preferred_user_id"] = normalize_preferred_user_id(
                normalized.get("preferred_user_id")
            )
            result[user_id] = normalized
        return result

    def _prune(self, data: dict[str, dict[str, Any]], now: datetime) -> None:
        cutoff = now.date() - timedelta(days=SIGNUP_RETENTION_DAYS)

        for guild_id in list(data):
            events = data[guild_id]
            for event_key in list(events):
                try:
                    event_date = key_date(event_key)
                except ValueError:
                    logging.warning(
                        "Signup store %s has malformed event key %r; dropping it.",
                        self._path,
                        event_key,
                    )
                    del events[event_key]
                    continue
                if event_date < cutoff:
                    del events[event_key]
            if not events:
                del data[guild_id]
