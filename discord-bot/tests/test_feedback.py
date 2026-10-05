import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bot.services.feedback import (
    FEEDBACK_RETENTION_ENTRIES,
    MAX_COMMENT_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    OTHER_COMMAND,
    FeedbackStore,
    clean_command,
    clean_comment,
    clean_display_name,
)


GUILD_ID = "123456789012345678"
OTHER_GUILD_ID = "987654321098765432"
NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "state" / "feedback.json")


def test_add_and_get_feedback_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)

    entry = store.add_feedback(
        GUILD_ID, "11", "Aaron", "rules query", "The excerpt was cut off.", now=NOW
    )
    entries = store.get_feedback(GUILD_ID)

    assert len(entries) == 1
    assert entries[0]["user_id"] == "11"
    assert entries[0]["display_name"] == "Aaron"
    assert entries[0]["command"] == "rules query"
    assert entries[0]["comment"] == "The excerpt was cut off."
    assert entries[0]["submitted_at"] == NOW.isoformat()
    assert entries[0]["status"] == "open"
    assert entries[0]["id"] == entry["id"]


def test_display_name_sanitised_before_storage(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_feedback(
        GUILD_ID,
        "11",
        "  Aaron\u200b\nHarrex  ",
        "rules query",
        "The excerpt was cut off.",
        now=NOW,
    )

    assert store.get_feedback(GUILD_ID)[0]["display_name"] == "Aaron Harrex"


def test_unknown_command_falls_back_to_other(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_feedback(GUILD_ID, "11", "Aaron", "/not-a-command", "general thought", now=NOW)

    assert store.get_feedback(GUILD_ID)[0]["command"] == OTHER_COMMAND


def test_feedback_is_scoped_per_guild(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_feedback(GUILD_ID, "11", "Aaron", "ping", "guild one", now=NOW)
    store.add_feedback(OTHER_GUILD_ID, "22", "Dave", "ping", "guild two", now=NOW)

    assert [e["comment"] for e in store.get_feedback(GUILD_ID)] == ["guild one"]
    assert [e["comment"] for e in store.get_feedback(OTHER_GUILD_ID)] == ["guild two"]


def test_entries_kept_in_submission_order(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_feedback(GUILD_ID, "11", "Aaron", "ping", "first", now=NOW)
    store.add_feedback(GUILD_ID, "22", "Dave", "ping", "second", now=NOW)

    assert [e["comment"] for e in store.get_feedback(GUILD_ID)] == ["first", "second"]


def test_retention_caps_entries_per_guild(tmp_path: Path) -> None:
    store = _store(tmp_path)

    total = FEEDBACK_RETENTION_ENTRIES + 5
    for index in range(total):
        store.add_feedback(GUILD_ID, "11", "Aaron", "ping", f"comment {index}", now=NOW)

    entries = store.get_feedback(GUILD_ID)
    assert len(entries) == FEEDBACK_RETENTION_ENTRIES
    assert entries[0]["comment"] == "comment 5"
    assert entries[-1]["comment"] == f"comment {total - 1}"


def test_get_feedback_unknown_guild_is_empty(tmp_path: Path) -> None:
    assert _store(tmp_path).get_feedback(GUILD_ID) == []


def test_corrupt_file_treated_as_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = tmp_path / "state" / "feedback.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")

    assert store.get_feedback(GUILD_ID) == []


def test_malformed_entries_ignored(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = tmp_path / "state" / "feedback.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({GUILD_ID: [{"comment": "good"}, "bad", 5]}), encoding="utf-8"
    )

    entries = store.get_feedback(GUILD_ID)
    assert [e["comment"] for e in entries] == ["good"]


def test_invalid_guild_id_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _store(tmp_path).add_feedback("not-a-snowflake", "11", "Aaron", "ping", "hi")


def test_clean_comment_strips_control_chars_and_collapses_whitespace() -> None:
    assert clean_comment("  the​  bot\nbroke\t ") == "the bot broke"


def test_clean_comment_rejects_empty() -> None:
    with pytest.raises(ValueError):
        clean_comment("   ​ ")


def test_clean_comment_truncates() -> None:
    assert len(clean_comment("a" * (MAX_COMMENT_LENGTH + 50))) == MAX_COMMENT_LENGTH


def test_clean_display_name_falls_back_when_empty() -> None:
    assert clean_display_name(" \u200b ") == "Unknown"


def test_clean_display_name_truncates() -> None:
    assert (
        len(clean_display_name("a" * (MAX_DISPLAY_NAME_LENGTH + 50)))
        == MAX_DISPLAY_NAME_LENGTH
    )


def test_clean_command_known_value_preserved() -> None:
    assert clean_command("club-night signup") == "club-night signup"


def test_clean_command_unknown_value_becomes_other() -> None:
    assert clean_command("/admin diagnostics") == OTHER_COMMAND
