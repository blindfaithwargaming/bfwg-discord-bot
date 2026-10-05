from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.services.ids import validate_guild_id
from bot.services.stadium_events import ClubNight
from bot.services.organised_play import adopt_legacy_event, event_key, key_date

PAIRING_CONSTRAINTS_RETENTION_DAYS = 60

LOCKS_KEY = "locks"
AVOIDS_KEY = "avoids"
BYE_KEY = "bye"


class PairingConstraintsStoreError(OSError):
    """Raised when the constraints store exists but cannot be read or parsed.

    Subclasses ``OSError`` so command-layer ``except OSError`` handlers refuse to
    pair (or persist) rather than silently treating corrupt authoritative state
    as "no constraints set".
    """


@dataclass(frozen=True)
class PairingConstraints:
    """Organiser-set constraints for a single club night's pairings."""

    locked_pairs: frozenset[frozenset[str]] = frozenset()
    avoided_pairs: frozenset[frozenset[str]] = frozenset()
    forced_bye: str | None = None


def _normalize_pair(player_a: str, player_b: str) -> list[str] | None:
    pair = sorted((str(player_a), str(player_b)))
    if pair[0] == pair[1]:
        return None
    return pair


def _dedupe_pairs(raw: Any) -> list[list[str]]:
    if not isinstance(raw, list):
        return []

    seen: set[tuple[str, str]] = set()
    result: list[list[str]] = []
    for entry in raw:
        if (
            not isinstance(entry, list)
            or len(entry) != 2
            or not all(isinstance(player, str) for player in entry)
        ):
            continue
        pair = sorted(entry)
        if pair[0] == pair[1]:
            continue
        key = (pair[0], pair[1])
        if key not in seen:
            seen.add(key)
            result.append(pair)
    return result


def _is_empty_event(record: dict[str, Any]) -> bool:
    bye = record.get(BYE_KEY)
    return (
        not _dedupe_pairs(record.get(LOCKS_KEY))
        and not _dedupe_pairs(record.get(AVOIDS_KEY))
        and not isinstance(bye, str)
    )


class PairingConstraintsStore:
    """Local JSON store of organiser pairing constraints.

    The bot runs as a single instance (see design rules), so a local file is
    sufficient — no distributed lock is needed. State is keyed by ``guild_id``
    then date/game/thread key (legacy keys are ISO dates), each holding ``locks``/``avoids`` (lists of sorted
    two-player lists) and a ``bye`` user id. Persisted as JSON, mirroring
    ``SignupStore`` in ``club_night_signups.py``.
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
        except json.JSONDecodeError as exc:
            logging.error(
                "Pairing constraints store %s is corrupt; refusing to read.",
                self._path,
            )
            raise PairingConstraintsStoreError(
                f"Pairing constraints store {self._path} is corrupt."
            ) from exc
        if not isinstance(data, dict):
            return {}

        cleaned: dict[str, dict[str, Any]] = {}
        malformed = False

        for guild_id, events in data.items():
            if not isinstance(events, dict):
                malformed = True
                continue

            cleaned_events: dict[str, dict[str, Any]] = {}
            for event_key, record in events.items():
                if not isinstance(record, dict):
                    malformed = True
                    continue
                cleaned_events[event_key] = record

            if cleaned_events:
                cleaned[guild_id] = cleaned_events

        if malformed:
            logging.warning(
                "Pairing constraints store %s has malformed entries; ignoring them.",
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

    def add_lock(
        self,
        guild_id: str,
        event_date: date,
        player_a: str,
        player_b: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._add_pair(guild_id, event_date, LOCKS_KEY, player_a, player_b, now=now)

    def remove_lock(
        self,
        guild_id: str,
        event_date: date,
        player_a: str,
        player_b: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._remove_pair(guild_id, event_date, LOCKS_KEY, player_a, player_b, now=now)

    def add_avoid(
        self,
        guild_id: str,
        event_date: date,
        player_a: str,
        player_b: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._add_pair(guild_id, event_date, AVOIDS_KEY, player_a, player_b, now=now)

    def remove_avoid(
        self,
        guild_id: str,
        event_date: date,
        player_a: str,
        player_b: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._remove_pair(guild_id, event_date, AVOIDS_KEY, player_a, player_b, now=now)

    def set_bye(
        self,
        guild_id: str,
        event_date: date,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        validated_guild_id = validate_guild_id(guild_id)
        data = self._load()
        event = data.setdefault(validated_guild_id, {}).setdefault(
            event_key(event_date, self.event), {}
        )
        event[BYE_KEY] = str(user_id)
        self._prune(data, now or datetime.now(UTC))
        self._save(data)

    def clear_bye(
        self,
        guild_id: str,
        event_date: date,
        *,
        now: datetime | None = None,
    ) -> None:
        validated_guild_id = validate_guild_id(guild_id)
        data = self._load()
        event = data.get(validated_guild_id, {}).get(event_key(event_date, self.event))
        if isinstance(event, dict):
            event[BYE_KEY] = None
        self._prune(data, now or datetime.now(UTC))
        self._save(data)

    def clear_event(
        self,
        guild_id: str,
        event_date: date,
        *,
        now: datetime | None = None,
    ) -> None:
        validated_guild_id = validate_guild_id(guild_id)
        data = self._load()
        events = data.get(validated_guild_id)
        if isinstance(events, dict):
            events.pop(event_key(event_date, self.event), None)
        self._prune(data, now or datetime.now(UTC))
        self._save(data)

    def get_constraints(
        self, guild_id: str, event_date: date
    ) -> PairingConstraints:
        validated_guild_id = validate_guild_id(guild_id)
        event = (
            self._load()
            .get(validated_guild_id, {})
            .get(event_key(event_date, self.event), {})
        )
        locks = frozenset(
            frozenset(pair) for pair in _dedupe_pairs(event.get(LOCKS_KEY))
        )
        avoids = frozenset(
            frozenset(pair) for pair in _dedupe_pairs(event.get(AVOIDS_KEY))
        )
        bye = event.get(BYE_KEY)
        return PairingConstraints(
            locked_pairs=locks,
            avoided_pairs=avoids,
            forced_bye=bye if isinstance(bye, str) else None,
        )

    def _add_pair(
        self,
        guild_id: str,
        event_date: date,
        key: str,
        player_a: str,
        player_b: str,
        *,
        now: datetime | None,
    ) -> None:
        validated_guild_id = validate_guild_id(guild_id)
        pair = _normalize_pair(player_a, player_b)
        if pair is None:
            raise ValueError("A pair must be two distinct players.")

        data = self._load()
        event = data.setdefault(validated_guild_id, {}).setdefault(
            event_key(event_date, self.event), {}
        )
        pairs = _dedupe_pairs(event.get(key))
        if pair not in pairs:
            pairs.append(pair)
        event[key] = sorted(pairs)
        self._prune(data, now or datetime.now(UTC))
        self._save(data)

    def _remove_pair(
        self,
        guild_id: str,
        event_date: date,
        key: str,
        player_a: str,
        player_b: str,
        *,
        now: datetime | None,
    ) -> None:
        validated_guild_id = validate_guild_id(guild_id)
        pair = _normalize_pair(player_a, player_b)
        data = self._load()
        event = data.get(validated_guild_id, {}).get(event_key(event_date, self.event))
        if isinstance(event, dict) and pair is not None:
            event[key] = sorted(p for p in _dedupe_pairs(event.get(key)) if p != pair)
        self._prune(data, now or datetime.now(UTC))
        self._save(data)

    def _prune(self, data: dict[str, dict[str, Any]], now: datetime) -> None:
        cutoff = now.date() - timedelta(days=PAIRING_CONSTRAINTS_RETENTION_DAYS)

        for guild_id in list(data):
            events = data[guild_id]
            for event_key in list(events):
                try:
                    event_date = key_date(event_key)
                except ValueError:
                    logging.warning(
                        "Pairing constraints store %s has malformed event key %r; "
                        "dropping it.",
                        self._path,
                        event_key,
                    )
                    del events[event_key]
                    continue
                if event_date < cutoff or _is_empty_event(events[event_key]):
                    del events[event_key]
            if not events:
                del data[guild_id]
