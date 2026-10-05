import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from bot.services.club_night_pairing_constraints import (
    PairingConstraints,
    PairingConstraintsStore,
    PairingConstraintsStoreError,
)


GUILD_ID = "123456789012345678"
OTHER_GUILD_ID = "987654321098765432"
EVENT_DATE = date(2026, 6, 25)
OTHER_DATE = date(2026, 7, 9)
NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> PairingConstraintsStore:
    return PairingConstraintsStore(tmp_path / "state" / "constraints.json")


def test_empty_store_returns_empty_constraints(tmp_path: Path) -> None:
    constraints = _store(tmp_path).get_constraints(GUILD_ID, EVENT_DATE)

    assert constraints == PairingConstraints()
    assert constraints.locked_pairs == frozenset()
    assert constraints.avoided_pairs == frozenset()
    assert constraints.forced_bye is None


def test_add_lock_round_trip_and_normalisation(tmp_path: Path) -> None:
    store = _store(tmp_path)

    # Order should not matter — pairs are stored sorted.
    store.add_lock(GUILD_ID, EVENT_DATE, "22", "11", now=NOW)

    constraints = store.get_constraints(GUILD_ID, EVENT_DATE)
    assert constraints.locked_pairs == frozenset({frozenset({"11", "22"})})


def test_add_lock_is_deduped(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_lock(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)
    store.add_lock(GUILD_ID, EVENT_DATE, "22", "11", now=NOW)

    constraints = store.get_constraints(GUILD_ID, EVENT_DATE)
    assert constraints.locked_pairs == frozenset({frozenset({"11", "22"})})


def test_remove_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_lock(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)
    store.add_lock(GUILD_ID, EVENT_DATE, "33", "44", now=NOW)

    store.remove_lock(GUILD_ID, EVENT_DATE, "22", "11", now=NOW)

    constraints = store.get_constraints(GUILD_ID, EVENT_DATE)
    assert constraints.locked_pairs == frozenset({frozenset({"33", "44"})})


def test_self_pair_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="two distinct players"):
        store.add_lock(GUILD_ID, EVENT_DATE, "11", "11", now=NOW)


def test_avoid_pairs_are_separate_from_locks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_lock(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)
    store.add_avoid(GUILD_ID, EVENT_DATE, "33", "44", now=NOW)

    constraints = store.get_constraints(GUILD_ID, EVENT_DATE)
    assert constraints.locked_pairs == frozenset({frozenset({"11", "22"})})
    assert constraints.avoided_pairs == frozenset({frozenset({"33", "44"})})

    store.remove_avoid(GUILD_ID, EVENT_DATE, "33", "44", now=NOW)
    assert store.get_constraints(GUILD_ID, EVENT_DATE).avoided_pairs == frozenset()


def test_set_and_clear_bye(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.set_bye(GUILD_ID, EVENT_DATE, "11", now=NOW)
    assert store.get_constraints(GUILD_ID, EVENT_DATE).forced_bye == "11"

    store.clear_bye(GUILD_ID, EVENT_DATE, now=NOW)
    assert store.get_constraints(GUILD_ID, EVENT_DATE).forced_bye is None


def test_per_guild_and_per_event_scoping(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_lock(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)
    store.add_avoid(OTHER_GUILD_ID, EVENT_DATE, "33", "44", now=NOW)
    store.set_bye(GUILD_ID, OTHER_DATE, "55", now=NOW)

    assert store.get_constraints(GUILD_ID, EVENT_DATE).locked_pairs == frozenset(
        {frozenset({"11", "22"})}
    )
    assert store.get_constraints(GUILD_ID, EVENT_DATE).forced_bye is None
    assert store.get_constraints(OTHER_GUILD_ID, EVENT_DATE).avoided_pairs == frozenset(
        {frozenset({"33", "44"})}
    )
    assert store.get_constraints(GUILD_ID, OTHER_DATE).forced_bye == "55"


def test_clear_event_removes_only_that_event(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_lock(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)
    store.set_bye(GUILD_ID, EVENT_DATE, "33", now=NOW)
    store.add_avoid(GUILD_ID, OTHER_DATE, "44", "55", now=NOW)

    store.clear_event(GUILD_ID, EVENT_DATE, now=NOW)

    assert store.get_constraints(GUILD_ID, EVENT_DATE) == PairingConstraints()
    assert store.get_constraints(GUILD_ID, OTHER_DATE).avoided_pairs == frozenset(
        {frozenset({"44", "55"})}
    )


def test_emptied_event_is_pruned_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "state" / "constraints.json"
    store = PairingConstraintsStore(path)
    store.add_lock(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)

    store.remove_lock(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)

    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_expired_events_are_pruned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old_date = date(2026, 1, 1)
    store.add_lock(GUILD_ID, old_date, "11", "22", now=NOW)

    # A later write at NOW prunes events older than the 60-day retention window.
    store.add_lock(GUILD_ID, EVENT_DATE, "33", "44", now=NOW)

    assert store.get_constraints(GUILD_ID, old_date).locked_pairs == frozenset()
    assert store.get_constraints(GUILD_ID, EVENT_DATE).locked_pairs == frozenset(
        {frozenset({"33", "44"})}
    )


def test_corrupt_file_blocks_reads(tmp_path: Path) -> None:
    path = tmp_path / "state" / "constraints.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{", encoding="utf-8")

    store = PairingConstraintsStore(path)
    with pytest.raises(PairingConstraintsStoreError):
        store.get_constraints(GUILD_ID, EVENT_DATE)


def test_corrupt_file_blocks_writes(tmp_path: Path) -> None:
    path = tmp_path / "state" / "constraints.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{", encoding="utf-8")

    store = PairingConstraintsStore(path)
    with pytest.raises(PairingConstraintsStoreError):
        store.add_lock(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)


def test_store_error_is_oserror() -> None:
    assert issubclass(PairingConstraintsStoreError, OSError)


def test_missing_file_returns_empty_constraints(tmp_path: Path) -> None:
    path = tmp_path / "state" / "constraints.json"

    assert PairingConstraintsStore(path).get_constraints(
        GUILD_ID, EVENT_DATE
    ) == PairingConstraints()


def test_malformed_entries_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "state" / "constraints.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                GUILD_ID: {
                    EVENT_DATE.isoformat(): {
                        "locks": [["11", "22"], ["bad"], "nope", ["11", "11"]],
                        "avoids": "not-a-list",
                        "bye": 5,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    constraints = PairingConstraintsStore(path).get_constraints(GUILD_ID, EVENT_DATE)
    assert constraints.locked_pairs == frozenset({frozenset({"11", "22"})})
    assert constraints.avoided_pairs == frozenset()
    assert constraints.forced_bye is None


def test_invalid_guild_id_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="snowflake"):
        store.add_lock("1", EVENT_DATE, "11", "22", now=NOW)
