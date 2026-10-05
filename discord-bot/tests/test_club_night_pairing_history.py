import json
from datetime import UTC, date, datetime
from pathlib import Path

from bot.services.club_night_pairing_history import (
    PAIRING_HISTORY_RETENTION_EVENTS,
    PairingHistoryStore,
)


GUILD_ID = "123456789012345678"
NOW = datetime(2026, 6, 26, 21, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> PairingHistoryStore:
    return PairingHistoryStore(tmp_path / "state" / "club_night_pairing_history.json")


def test_record_and_read_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.record_pairings(
        GUILD_ID, date(2026, 6, 11), [("11", "22"), ("33", "44")], now=NOW
    )
    recent = store.get_recent_opponents(GUILD_ID, date(2026, 6, 25), 2)

    assert recent == frozenset({frozenset({"11", "22"}), frozenset({"33", "44"})})


def test_record_stores_pairs_sorted_and_records_timestamp(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.record_pairings(GUILD_ID, date(2026, 6, 11), [("22", "11")], now=NOW)

    data = json.loads(
        (tmp_path / "state" / "club_night_pairing_history.json").read_text(
            encoding="utf-8"
        )
    )
    record = data[GUILD_ID]["2026-06-11"]
    assert record["pairs"] == [["11", "22"]]
    assert record["recorded_at"] == NOW.isoformat()


def test_re_recording_same_date_replaces_previous(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.record_pairings(GUILD_ID, date(2026, 6, 11), [("11", "22")], now=NOW)
    store.record_pairings(GUILD_ID, date(2026, 6, 11), [("11", "33")], now=NOW)

    recent = store.get_recent_opponents(GUILD_ID, date(2026, 6, 25), 2)
    assert recent == frozenset({frozenset({"11", "33"})})


def test_get_recent_opponents_lookback_limits_dates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_pairings(GUILD_ID, date(2026, 5, 14), [("11", "22")], now=NOW)
    store.record_pairings(GUILD_ID, date(2026, 5, 28), [("33", "44")], now=NOW)
    store.record_pairings(GUILD_ID, date(2026, 6, 11), [("55", "66")], now=NOW)

    recent = store.get_recent_opponents(GUILD_ID, date(2026, 6, 25), 2)

    assert recent == frozenset({frozenset({"33", "44"}), frozenset({"55", "66"})})


def test_get_recent_opponents_lookback_one_returns_most_recent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_pairings(GUILD_ID, date(2026, 5, 14), [("11", "22")], now=NOW)
    store.record_pairings(GUILD_ID, date(2026, 5, 28), [("33", "44")], now=NOW)
    store.record_pairings(GUILD_ID, date(2026, 6, 11), [("55", "66")], now=NOW)

    recent = store.get_recent_opponents(GUILD_ID, date(2026, 6, 25), 1)

    assert recent == frozenset({frozenset({"55", "66"})})


def test_get_recent_opponents_lookback_beyond_history_returns_all(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.record_pairings(GUILD_ID, date(2026, 5, 28), [("11", "22")], now=NOW)
    store.record_pairings(GUILD_ID, date(2026, 6, 11), [("33", "44")], now=NOW)

    recent = store.get_recent_opponents(GUILD_ID, date(2026, 6, 25), 5)

    assert recent == frozenset({frozenset({"11", "22"}), frozenset({"33", "44"})})


def test_get_recent_opponents_lookback_zero_returns_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_pairings(GUILD_ID, date(2026, 6, 11), [("11", "22")], now=NOW)

    assert store.get_recent_opponents(GUILD_ID, date(2026, 6, 25), 0) == frozenset()


def test_get_recent_opponents_excludes_before_date_itself(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_pairings(GUILD_ID, date(2026, 6, 11), [("11", "22")], now=NOW)
    store.record_pairings(GUILD_ID, date(2026, 6, 25), [("33", "44")], now=NOW)

    recent = store.get_recent_opponents(GUILD_ID, date(2026, 6, 25), 2)

    assert recent == frozenset({frozenset({"11", "22"})})


def test_get_recent_opponents_unknown_guild_returns_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_pairings(GUILD_ID, date(2026, 6, 11), [("11", "22")], now=NOW)

    other = "987654321098765432"
    assert store.get_recent_opponents(other, date(2026, 6, 25), 2) == frozenset()


def test_corrupt_json_degrades_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "state" / "club_night_pairing_history.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    store = PairingHistoryStore(path)

    assert store.get_recent_opponents(GUILD_ID, date(2026, 6, 25), 2) == frozenset()


def test_malformed_guild_entries_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "state" / "club_night_pairing_history.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                GUILD_ID: {"2026-06-11": {"pairs": [["11", "22"]]}},
                "bad-guild": ["not", "a", "dict"],
            }
        ),
        encoding="utf-8",
    )
    store = PairingHistoryStore(path)

    recent = store.get_recent_opponents(GUILD_ID, date(2026, 6, 25), 2)
    assert recent == frozenset({frozenset({"11", "22"})})


def test_malformed_records_and_pairs_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "state" / "club_night_pairing_history.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                GUILD_ID: {
                    "2026-05-14": "not-a-dict",
                    "2026-05-28": {"pairs": "not-a-list"},
                    "2026-06-11": {
                        "pairs": [
                            ["11", "22"],
                            ["only-one"],
                            ["33", 44],
                            ["55", "66"],
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    store = PairingHistoryStore(path)

    recent = store.get_recent_opponents(GUILD_ID, date(2026, 6, 25), 5)
    assert recent == frozenset({frozenset({"11", "22"}), frozenset({"55", "66"})})


def test_prune_keeps_only_retention_count(tmp_path: Path) -> None:
    store = _store(tmp_path)
    total = PAIRING_HISTORY_RETENTION_EVENTS + 5
    for index in range(total):
        store.record_pairings(
            GUILD_ID, date(2026, 1, 1) + _days(index * 14), [("11", "22")], now=NOW
        )

    data = json.loads(
        (tmp_path / "state" / "club_night_pairing_history.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(data[GUILD_ID]) == PAIRING_HISTORY_RETENTION_EVENTS


def test_atomic_save_leaves_no_tmp_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_pairings(GUILD_ID, date(2026, 6, 11), [("11", "22")], now=NOW)

    state_dir = tmp_path / "state"
    assert not list(state_dir.glob("*.tmp"))


def _days(count: int):
    from datetime import timedelta

    return timedelta(days=count)
