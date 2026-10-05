import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from bot.services.club_night_signups import (
    SignupStore,
    normalize_format_pref,
    normalize_side,
    validate_format_pref,
    validate_side,
)


GUILD_ID = "123456789012345678"
EVENT_DATE = date(2026, 6, 25)
NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> SignupStore:
    return SignupStore(tmp_path / "state" / "club_night_signups.json")


def test_add_and_get_signup_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", now=NOW)
    signups = store.get_signups(GUILD_ID, EVENT_DATE)

    assert set(signups) == {"11"}
    assert signups["11"]["display_name"] == "Aaron"
    assert signups["11"]["signed_up_at"] == NOW.isoformat()
    assert signups["11"]["side"] == "either"
    assert signups["11"]["preferred_user_id"] is None


def test_add_signup_records_side(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", side="loyalist", now=NOW)
    store.add_signup(GUILD_ID, EVENT_DATE, "22", "Dave", side="Traitor", now=NOW)

    signups = store.get_signups(GUILD_ID, EVENT_DATE)
    assert signups["11"]["side"] == "loyalist"
    assert signups["22"]["side"] == "traitor"


def test_add_signup_rejects_unknown_side(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="side must be one of"):
        store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", side="xenos", now=NOW)


def test_validate_side_normalizes_case_and_whitespace() -> None:
    assert validate_side("  Loyalist ") == "loyalist"
    assert validate_side("EITHER") == "either"


def test_duplicate_signup_overwrites_without_duplicating(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", side="loyalist", now=NOW)
    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron H", side="traitor", now=NOW)

    signups = store.get_signups(GUILD_ID, EVENT_DATE)
    assert set(signups) == {"11"}
    assert signups["11"]["display_name"] == "Aaron H"
    assert signups["11"]["side"] == "traitor"


def test_signup_defaults_to_solo_format(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", now=NOW)

    assert store.get_signups(GUILD_ID, EVENT_DATE)["11"]["format_pref"] == "solo"


def test_add_signup_records_format_pref(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_signup(
        GUILD_ID, EVENT_DATE, "11", "Aaron", format_pref="Doubles", now=NOW
    )

    assert store.get_signups(GUILD_ID, EVENT_DATE)["11"]["format_pref"] == "doubles"


def test_add_signup_rejects_unknown_format(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="format must be one of"):
        store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", format_pref="quad")


def test_resignup_without_format_preserves_existing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", format_pref="doubles", now=NOW)

    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", side="loyalist", now=NOW)

    assert store.get_signups(GUILD_ID, EVENT_DATE)["11"]["format_pref"] == "doubles"


def test_set_format_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", now=NOW)

    store.set_format(GUILD_ID, EVENT_DATE, "11", "either", now=NOW)

    assert store.get_signups(GUILD_ID, EVENT_DATE)["11"]["format_pref"] == "either"


def test_set_format_raises_for_unsigned_user(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(KeyError):
        store.set_format(GUILD_ID, EVENT_DATE, "11", "doubles", now=NOW)


def test_legacy_signup_without_format_reads_as_solo(tmp_path: Path) -> None:
    path = tmp_path / "club_night_signups.json"
    path.write_text(
        json.dumps(
            {
                GUILD_ID: {
                    EVENT_DATE.isoformat(): {
                        "11": {
                            "display_name": "Aaron",
                            "signed_up_at": NOW.isoformat(),
                            "side": "loyalist",
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    store = SignupStore(path)

    entry = store.get_signups(GUILD_ID, EVENT_DATE)["11"]
    assert entry["format_pref"] == "solo"


def test_resignup_preserves_unknown_future_fields(tmp_path: Path) -> None:
    path = tmp_path / "club_night_signups.json"
    path.write_text(
        json.dumps(
            {
                GUILD_ID: {
                    EVENT_DATE.isoformat(): {
                        "11": {
                            "display_name": "Aaron",
                            "signed_up_at": NOW.isoformat(),
                            "side": "either",
                            "future_team_size": 3,
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    store = SignupStore(path)

    store.add_signup(
        GUILD_ID,
        EVENT_DATE,
        "11",
        "Aaron",
        side="loyalist",
        now=NOW,
    )

    entry = store.get_signups(GUILD_ID, EVENT_DATE)["11"]
    assert entry["future_team_size"] == 3
    assert entry["side"] == "loyalist"
    assert entry["format_pref"] == "solo"


def test_validate_and_normalize_format_pref() -> None:
    assert validate_format_pref("  Solo ") == "solo"
    assert normalize_format_pref("DOUBLES") == "doubles"
    assert normalize_format_pref(None) == "solo"
    assert normalize_format_pref("nonsense") == "solo"
    assert validate_side(" Loyalist ") == "loyalist"
    assert normalize_side("TRAITOR") == "traitor"
    assert normalize_side(None) == "either"
    assert normalize_side("xenos") == "either"

    with pytest.raises(ValueError, match="format must be one of"):
        validate_format_pref(None)
    with pytest.raises(ValueError, match="side must be one of"):
        validate_side(None)


def test_read_boundary_canonicalizes_known_fields_and_preserves_unknowns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "club_night_signups.json"
    path.write_text(
        json.dumps(
            {
                GUILD_ID: {
                    EVENT_DATE.isoformat(): {
                        "11": {
                            "display_name": "Aaron",
                            "signed_up_at": NOW.isoformat(),
                            "side": "xenos",
                            "format_pref": "narrative",
                            "preferred_user_id": " ",
                            "future_team_size": 3,
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    store = SignupStore(path)
    entry = store.get_signups(GUILD_ID, EVENT_DATE)["11"]

    assert entry["side"] == "either"
    assert entry["format_pref"] == "solo"
    assert entry["preferred_user_id"] is None
    assert entry["future_team_size"] == 3

    store.set_format(GUILD_ID, EVENT_DATE, "11", "doubles", now=NOW)
    updated = store.get_signups(GUILD_ID, EVENT_DATE)["11"]
    assert updated["format_pref"] == "doubles"
    assert updated["future_team_size"] == 3


def test_resignup_preserves_existing_preference(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", now=NOW)
    store.set_preference(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)

    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", side="loyalist", now=NOW)

    assert store.get_signups(GUILD_ID, EVENT_DATE)["11"]["preferred_user_id"] == "22"


def test_remove_signup_clears_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", now=NOW)
    store.set_preference(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)

    assert store.remove_signup(GUILD_ID, EVENT_DATE, "11", now=NOW) is True
    assert store.get_signups(GUILD_ID, EVENT_DATE) == {}


def test_remove_signup_returns_false_when_not_signed_up(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.remove_signup(GUILD_ID, EVENT_DATE, "11", now=NOW) is False


def test_set_preference_round_trip_and_clear(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", now=NOW)

    store.set_preference(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)
    assert store.get_signups(GUILD_ID, EVENT_DATE)["11"]["preferred_user_id"] == "22"

    store.set_preference(GUILD_ID, EVENT_DATE, "11", None, now=NOW)
    assert store.get_signups(GUILD_ID, EVENT_DATE)["11"]["preferred_user_id"] is None


def test_set_preference_raises_for_unsigned_user(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(KeyError):
        store.set_preference(GUILD_ID, EVENT_DATE, "11", "22", now=NOW)


def test_set_side_overrides_recorded_side(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", side="loyalist", now=NOW)

    store.set_side(GUILD_ID, EVENT_DATE, "11", "Traitor", now=NOW)

    assert store.get_signups(GUILD_ID, EVENT_DATE)["11"]["side"] == "traitor"


def test_set_side_raises_for_unsigned_user(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(KeyError):
        store.set_side(GUILD_ID, EVENT_DATE, "11", "loyalist", now=NOW)


def test_set_side_rejects_unknown_side(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", now=NOW)

    with pytest.raises(ValueError, match="side must be one of"):
        store.set_side(GUILD_ID, EVENT_DATE, "11", "xenos", now=NOW)


def test_clear_event_removes_only_that_guild_and_event(tmp_path: Path) -> None:
    store = _store(tmp_path)
    other_date = date(2026, 7, 9)
    other_guild = "987654321098765432"
    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", now=NOW)
    store.add_signup(GUILD_ID, EVENT_DATE, "22", "Dave", now=NOW)
    store.add_signup(GUILD_ID, other_date, "33", "Cei", now=NOW)
    store.add_signup(other_guild, EVENT_DATE, "44", "Emrys", now=NOW)

    removed = store.clear_event(GUILD_ID, EVENT_DATE, now=NOW)

    assert removed == 2
    assert store.get_signups(GUILD_ID, EVENT_DATE) == {}
    assert set(store.get_signups(GUILD_ID, other_date)) == {"33"}
    assert set(store.get_signups(other_guild, EVENT_DATE)) == {"44"}


def test_clear_event_returns_zero_when_nothing_to_clear(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.clear_event(GUILD_ID, EVENT_DATE, now=NOW) == 0


def test_invalid_guild_id_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="snowflake"):
        store.add_signup("not-a-guild", EVENT_DATE, "11", "Aaron", now=NOW)


def test_corrupt_json_degrades_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "club_night_signups.json"
    path.write_text("{not json", encoding="utf-8")
    store = SignupStore(path)

    assert store.get_signups(GUILD_ID, EVENT_DATE) == {}

    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", now=NOW)
    assert set(store.get_signups(GUILD_ID, EVENT_DATE)) == {"11"}


def test_malformed_guild_entry_ignored(tmp_path: Path) -> None:
    path = tmp_path / "club_night_signups.json"
    path.write_text(
        json.dumps({GUILD_ID: "not-a-mapping"}),
        encoding="utf-8",
    )
    store = SignupStore(path)

    assert store.get_signups(GUILD_ID, EVENT_DATE) == {}


def test_malformed_event_entry_ignored(tmp_path: Path) -> None:
    path = tmp_path / "club_night_signups.json"
    path.write_text(
        json.dumps(
            {
                GUILD_ID: {
                    EVENT_DATE.isoformat(): "not-a-signup-mapping",
                    "2026-07-09": {
                        "22": {
                            "display_name": "Dave",
                            "signed_up_at": NOW.isoformat(),
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    store = SignupStore(path)

    assert store.get_signups(GUILD_ID, EVENT_DATE) == {}
    assert set(store.get_signups(GUILD_ID, date(2026, 7, 9))) == {"22"}


def test_malformed_signup_entry_ignored(tmp_path: Path) -> None:
    path = tmp_path / "club_night_signups.json"
    path.write_text(
        json.dumps(
            {
                GUILD_ID: {
                    EVENT_DATE.isoformat(): {
                        "11": "not-a-signup-entry",
                        "22": {
                            "display_name": "Dave",
                            "signed_up_at": NOW.isoformat(),
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    store = SignupStore(path)

    signups = store.get_signups(GUILD_ID, EVENT_DATE)
    assert set(signups) == {"22"}
    assert signups["22"]["display_name"] == "Dave"


def test_prune_drops_old_events_and_keeps_recent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old_event = date(2026, 3, 14)  # 90 days before NOW
    recent_event = date(2026, 6, 2)  # 10 days before NOW
    store.add_signup(GUILD_ID, old_event, "11", "Aaron", now=NOW)
    store.add_signup(GUILD_ID, recent_event, "22", "Dave", now=NOW)

    store.add_signup(GUILD_ID, EVENT_DATE, "33", "Cei", now=NOW)

    assert store.get_signups(GUILD_ID, old_event) == {}
    assert set(store.get_signups(GUILD_ID, recent_event)) == {"22"}
    assert set(store.get_signups(GUILD_ID, EVENT_DATE)) == {"33"}


def test_atomic_save_leaves_no_tmp_files(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.add_signup(GUILD_ID, EVENT_DATE, "11", "Aaron", now=NOW)
    store.remove_signup(GUILD_ID, EVENT_DATE, "11", now=NOW)

    state_dir = tmp_path / "state"
    assert [p.name for p in state_dir.iterdir()] == ["club_night_signups.json"]
