from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from bot.services.ids import validate_guild_id
from bot.services.stadium_events import ClubNight
from bot.services.organised_play import (
    adopt_legacy_event,
    event_key,
    key_date,
    key_game,
    group_signups,
    signup_game,
)

PAIRING_HISTORY_RETENTION_EVENTS = 12


class PairingHistoryStore:
    """Local JSON store of posted club-night pairings.

    The bot runs as a single instance (see design rules), so a local file is
    sufficient — no distributed lock is needed. State is keyed by ``guild_id``
    then date/game/thread key (legacy keys are ISO dates), each holding the posted ``pairs`` and a
    ``recorded_at`` timestamp. Persisted as JSON, mirroring ``SignupStore`` in
    ``club_night_signups.py``.
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
                "Pairing history store %s is corrupt; treating as empty.", self._path
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
            for event_key, record in events.items():
                if not isinstance(record, dict):
                    malformed = True
                    continue
                cleaned_events[event_key] = record

            if cleaned_events:
                cleaned[guild_id] = cleaned_events

        if malformed:
            logging.warning(
                "Pairing history store %s has malformed entries; ignoring them.",
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

    def record_pairings(
        self,
        guild_id: str,
        event_date: date,
        pairs: Iterable[tuple[str, str]],
        *,
        now: datetime | None = None,
        signups: dict[str, dict] | None = None,
    ) -> None:
        validated_guild_id = validate_guild_id(guild_id)
        target_now = now or datetime.now(UTC)
        stored_pairs = [sorted((str(a), str(b))) for a, b in pairs]
        data = self._load()
        events = data.setdefault(validated_guild_id, {})
        if signups is None or self.event is None:
            events[event_key(event_date, self.event)] = {
                "pairs": stored_pairs,
                "recorded_at": target_now.isoformat(),
            }
        else:
            groups = group_signups(signups, self.event.game)
            grouped_pairs = {game: [] for game in groups}
            for a, b in stored_pairs:
                game = signup_game(signups[a], self.event.game)
                if game != signup_game(signups[b], self.event.game):
                    raise ValueError("Players of different games cannot be paired.")
                grouped_pairs[game].append([a, b])
            # A repost replaces all games for this thread, including a game
            # which now has no players, without erasing another thread's pairs.
            for key in list(events):
                parts = key.split("|")
                if (
                    len(parts) == 3
                    and parts[0] == event_date.isoformat()
                    and parts[2] == self.event.thread_id
                ):
                    del events[key]
            for game, game_pairs in grouped_pairs.items():
                scope = replace(self.event, game=game)
                events[event_key(event_date, scope)] = {
                    "pairs": game_pairs,
                    "recorded_at": target_now.isoformat(),
                }
        self._prune(data)
        self._save(data)

    def get_recent_opponents(
        self,
        guild_id: str,
        before_date: date,
        lookback: int,
        *,
        game: str | None = None,
    ) -> frozenset[frozenset[str]]:
        if lookback <= 0:
            return frozenset()

        validated_guild_id = validate_guild_id(guild_id)
        events = self._load().get(validated_guild_id, {})

        prior_dates = sorted(
            key
            for key in events
            if self._event_before(key, before_date)
            and key_game(key) == (game or (self.event.game if self.event else "heresy"))
        )
        recent_keys = prior_dates[-lookback:]

        opponents: set[frozenset[str]] = set()
        for key in recent_keys:
            record = events.get(key)
            if not isinstance(record, dict):
                logging.warning(
                    "Pairing history store %s has a malformed record for %s; skipping.",
                    self._path,
                    key,
                )
                continue
            raw_pairs = record.get("pairs")
            if not isinstance(raw_pairs, list):
                logging.warning(
                    "Pairing history store %s has malformed pairs for %s; skipping.",
                    self._path,
                    key,
                )
                continue
            for pair in raw_pairs:
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or not all(isinstance(player, str) for player in pair)
                ):
                    logging.warning(
                        "Pairing history store %s has a malformed pair entry for %s; "
                        "skipping it.",
                        self._path,
                        key,
                    )
                    continue
                opponents.add(frozenset(pair))

        return frozenset(opponents)

    def get_recent_for_signups(
        self,
        guild_id: str,
        before_date: date,
        lookback: int,
        signups: dict[str, dict],
    ) -> frozenset[frozenset[str]]:
        groups = group_signups(signups, self.event.game if self.event else "heresy")
        opponents: set[frozenset[str]] = set()
        for game, players in groups.items():
            recent = self.get_recent_opponents(
                guild_id, before_date, lookback, game=game
            )
            opponents.update(pair for pair in recent if pair <= players.keys())
        return frozenset(opponents)

    def _event_before(self, event_key: str, before_date: date) -> bool:
        try:
            event_date = key_date(event_key)
        except ValueError:
            return False
        return event_date < before_date

    def _prune(self, data: dict[str, dict[str, Any]]) -> None:
        for guild_id in list(data):
            events = data[guild_id]
            for event_key in list(events):
                try:
                    key_date(event_key)
                except ValueError:
                    logging.warning(
                        "Pairing history store %s has malformed event key %r; "
                        "dropping it.",
                        self._path,
                        event_key,
                    )
                    del events[event_key]
            games = {key_game(key) for key in events}
            keep = {
                key
                for game in games
                for key in sorted(key for key in events if key_game(key) == game)[
                    -PAIRING_HISTORY_RETENTION_EVENTS:
                ]
            }
            for event_key in list(events):
                if event_key not in keep:
                    del events[event_key]
            if not events:
                del data[guild_id]
