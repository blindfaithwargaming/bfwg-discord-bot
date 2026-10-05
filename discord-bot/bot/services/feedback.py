from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.services.ids import validate_guild_id
from bot.services.text_utils import clean_user_text


FEEDBACK_RETENTION_ENTRIES = 500
MAX_COMMENT_LENGTH = 1000
MAX_DISPLAY_NAME_LENGTH = 80
OTHER_COMMAND = "other"

# Single source of truth for the commands a member can file feedback against:
# ``(label, value)`` pairs in display order. The `/feedback submit` choices and
# the ``KNOWN_COMMANDS`` allowlist both derive from this, so they cannot drift.
# Anything not listed here is filed under ``other``.
FEEDBACK_COMMAND_CHOICES: tuple[tuple[str, str], ...] = (
    ("/ping", "ping"),
    ("/stadium-event", "stadium-event"),
    ("/post-club-night", "post-club-night"),
    ("/club-night signup", "club-night signup"),
    ("/club-night my-signup", "club-night my-signup"),
    ("/club-night withdraw", "club-night withdraw"),
    ("/club-night prefer", "club-night prefer"),
    ("/club-night report-game", "club-night report-game"),
    ("/club-night remove-game", "club-night remove-game"),
    ("/club-night games", "club-night games"),
    ("/lore random", "lore random"),
    ("/lore search", "lore search"),
    ("/source", "source"),
    ("/rules query", "rules query"),
    ("/mission random", "mission random"),
    ("/scenario seed", "scenario seed"),
    ("/scenario twist", "scenario twist"),
    ("/name generator", "name generator"),
    ("Other / the bot in general", OTHER_COMMAND),
)

KNOWN_COMMANDS: frozenset[str] = frozenset(
    value for _, value in FEEDBACK_COMMAND_CHOICES
)


def clean_comment(value: str) -> str:
    """Sanitise a user-supplied comment before it is stored.

    The stored file is later read by an out-of-bot agent command, so input is
    bounded here rather than relying only on Discord-display escaping: control
    and format characters (zero-width joiners, bidi overrides, NULs) are dropped
    and surrounding/duplicate whitespace collapsed. Discord responses that echo
    the comment must still pass it through ``escape_public_text`` per design
    rule 3.
    """
    cleaned = clean_user_text(value)

    if not cleaned:
        raise ValueError("Please include a comment.")

    return cleaned[:MAX_COMMENT_LENGTH]


def clean_display_name(value: str) -> str:
    cleaned = clean_user_text(value)
    return (cleaned or "Unknown")[:MAX_DISPLAY_NAME_LENGTH]


def clean_command(value: str) -> str:
    cleaned = value.strip()
    return cleaned if cleaned in KNOWN_COMMANDS else OTHER_COMMAND


class FeedbackStore:
    """Local JSON store of user-submitted bug reports and suggestions.

    The bot runs as a single instance (see design rules), so a local file is
    sufficient — no distributed lock is needed. State is keyed by ``guild_id``
    holding a chronological list of feedback entries, persisted as JSON,
    mirroring ``PairingHistoryStore`` in ``club_night_pairing_history.py``.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logging.warning(
                "Feedback store %s is corrupt; treating as empty.", self._path
            )
            return {}
        if not isinstance(data, dict):
            return {}

        cleaned: dict[str, list[dict[str, Any]]] = {}
        malformed = False

        for guild_id, entries in data.items():
            if not isinstance(entries, list):
                malformed = True
                continue

            cleaned_entries = [entry for entry in entries if isinstance(entry, dict)]
            if len(cleaned_entries) != len(entries):
                malformed = True
            if cleaned_entries:
                cleaned[guild_id] = cleaned_entries

        if malformed:
            logging.warning(
                "Feedback store %s has malformed entries; ignoring them.",
                self._path,
            )
        return cleaned

    def _save(self, data: dict[str, list[dict[str, Any]]]) -> None:
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

    def add_feedback(
        self,
        guild_id: str,
        user_id: str,
        display_name: str,
        command: str,
        comment: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        validated_guild_id = validate_guild_id(guild_id)
        target_now = now or datetime.now(UTC)
        entry = {
            "id": uuid.uuid4().hex,
            "user_id": str(user_id),
            "display_name": clean_display_name(display_name),
            "command": clean_command(command),
            "comment": clean_comment(comment),
            "submitted_at": target_now.isoformat(),
            "status": "open",
        }
        data = self._load()
        entries = data.setdefault(validated_guild_id, [])
        entries.append(entry)
        self._prune(data)
        self._save(data)
        return entry

    def get_feedback(self, guild_id: str) -> list[dict[str, Any]]:
        validated_guild_id = validate_guild_id(guild_id)
        return [dict(entry) for entry in self._load().get(validated_guild_id, [])]

    def _prune(self, data: dict[str, list[dict[str, Any]]]) -> None:
        for guild_id in list(data):
            entries = data[guild_id]
            if len(entries) > FEEDBACK_RETENTION_ENTRIES:
                data[guild_id] = entries[-FEEDBACK_RETENTION_ENTRIES:]
            if not data[guild_id]:
                del data[guild_id]
