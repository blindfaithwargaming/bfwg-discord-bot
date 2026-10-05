import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from bot.services.club_night_games import (
    CAMPAIGN_HEX_MAX,
    CAMPAIGN_HEX_MIN,
    GAMES_SCHEMA_VERSION,
    MAX_FACTION_LENGTH,
    MAX_MISSION_LENGTH,
    MAX_NOTES_LENGTH,
    POINTS_SIZE_MAX,
    POINTS_SIZE_MIN,
    AmbiguousGameIdError,
    GameNotFoundError,
    GameRemovalNotAllowedError,
    GameStore,
    clean_faction,
    clean_mission,
    clean_notes,
    validate_campaign_hex,
    validate_points_size,
    validate_result,
)


GUILD_ID = "123456789012345678"
OTHER_GUILD_ID = "876543210987654321"
EVENT_DATE = date(2026, 7, 8)
THREAD_ID = "1391234567890123456"
THREAD_TITLE = "Organised Play - 8th July"
NOW = datetime(2026, 7, 8, 21, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> GameStore:
    return GameStore(tmp_path / "state" / "club_night_games.json")


def _add_game(store: GameStore, **overrides) -> dict:
    kwargs = {
        "guild_id": GUILD_ID,
        "club_night_date": EVENT_DATE,
        "thread_id": THREAD_ID,
        "thread_title": THREAD_TITLE,
        "reported_by_user_id": "11",
        "result": "side1",
        "side1": [("11", "Aaron", "Sons of Horus")],
        "side2": [("22", "Dave", "Imperial Fists")],
        "now": NOW,
    }
    kwargs.update(overrides)
    return store.add_game(**kwargs)


def test_add_and_get_game_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)

    entry = _add_game(store)
    games = store.get_games_for_thread(GUILD_ID, THREAD_ID)

    assert len(games) == 1
    game = games[0]
    assert game["id"] == entry["id"]
    assert game["guild_id"] == GUILD_ID
    assert game["club_night_date"] == "2026-07-08"
    assert game["thread_id"] == THREAD_ID
    assert game["thread_title"] == THREAD_TITLE
    assert game["reported_by_user_id"] == "11"
    assert game["reported_at"] == NOW.isoformat()
    assert game["game_type"] == "sides"
    assert game["result"] == "side1"
    assert game["points_size"] is None
    assert game["mission"] is None
    assert game["notes"] is None
    assert game["campaign_hex"] is None
    assert game["players"] == [
        {
            "user_id": "11",
            "display_name": "Aaron",
            "faction": "Sons of Horus",
            "side": "side1",
            "result": "win",
        },
        {
            "user_id": "22",
            "display_name": "Dave",
            "faction": "Imperial Fists",
            "side": "side2",
            "result": "loss",
        },
    ]


def test_saved_file_carries_schema_version_and_is_formatted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add_game(store)

    path = tmp_path / "state" / "club_night_games.json"
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)

    assert data["schema_version"] == GAMES_SCHEMA_VERSION
    assert isinstance(data["games"], list)
    assert raw.startswith("{\n")


def test_per_player_results_for_each_outcome(tmp_path: Path) -> None:
    store = _store(tmp_path)

    side2_win = _add_game(store, result="side2")
    draw = _add_game(store, result="draw")

    assert [p["result"] for p in side2_win["players"]] == ["loss", "win"]
    assert [p["result"] for p in draw["players"]] == ["draw", "draw"]


def test_multiplayer_sides_record_every_player(tmp_path: Path) -> None:
    store = _store(tmp_path)

    entry = _add_game(
        store,
        side1=[("11", "Aaron", "Sons of Horus"), ("33", "Beth", "World Eaters")],
        side2=[
            ("22", "Dave", "Imperial Fists"),
            ("44", "Carl", "Blood Angels"),
            ("55", "Eve", "Salamanders"),
        ],
    )

    assert [p["side"] for p in entry["players"]] == [
        "side1",
        "side1",
        "side2",
        "side2",
        "side2",
    ]
    assert [p["result"] for p in entry["players"]] == [
        "win",
        "win",
        "loss",
        "loss",
        "loss",
    ]


def test_optional_fields_are_cleaned_and_stored(tmp_path: Path) -> None:
    store = _store(tmp_path)

    entry = _add_game(
        store,
        points_size=3000,
        mission="  Onslaught \u200b ",
        notes="Tabled by turn   4.",
        campaign_hex=12,
    )

    assert entry["points_size"] == 3000
    assert entry["mission"] == "Onslaught"
    assert entry["notes"] == "Tabled by turn 4."
    assert entry["campaign_hex"] == 12


def test_add_game_rejects_unknown_result(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="result must be one of"):
        _add_game(_store(tmp_path), result="side3")


def test_add_game_rejects_empty_side(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="between 1 and 4"):
        _add_game(_store(tmp_path), side1=[])


def test_add_game_rejects_oversized_side(tmp_path: Path) -> None:
    side = [(str(10 + n), f"P{n}", "Sons of Horus") for n in range(5)]

    with pytest.raises(ValueError, match="between 1 and 4"):
        _add_game(_store(tmp_path), side1=side)


def test_add_game_rejects_duplicate_players(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only appear once"):
        _add_game(
            _store(tmp_path),
            side1=[("11", "Aaron", "Sons of Horus")],
            side2=[("11", "Aaron", "Imperial Fists")],
        )


def test_add_game_rejects_invalid_guild_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="snowflake"):
        _add_game(_store(tmp_path), guild_id="not-a-guild")


def test_validate_result_normalizes_case_and_whitespace() -> None:
    assert validate_result("  Side1 ") == "side1"
    assert validate_result("DRAW") == "draw"


def test_clean_faction_bounds_and_strips_control_characters() -> None:
    assert clean_faction("Sons\u200b of Horus") == "Sons of Horus"
    assert len(clean_faction("x" * 500)) == MAX_FACTION_LENGTH

    with pytest.raises(ValueError, match="faction"):
        clean_faction(" \u200b ")


def test_clean_mission_and_notes_bound_and_map_blank_to_none() -> None:
    assert clean_mission(None) is None
    assert clean_mission(" \u200b ") is None
    assert len(clean_mission("m" * 500)) == MAX_MISSION_LENGTH
    assert clean_notes(None) is None
    assert clean_notes("  ") is None
    assert len(clean_notes("n" * 2000)) == MAX_NOTES_LENGTH


def test_validate_points_size_bounds() -> None:
    assert validate_points_size(None) is None
    assert validate_points_size(POINTS_SIZE_MIN) == POINTS_SIZE_MIN
    assert validate_points_size(POINTS_SIZE_MAX) == POINTS_SIZE_MAX

    with pytest.raises(ValueError, match="Points size"):
        validate_points_size(POINTS_SIZE_MIN - 1)
    with pytest.raises(ValueError, match="Points size"):
        validate_points_size(POINTS_SIZE_MAX + 1)


def test_validate_campaign_hex_bounds() -> None:
    assert validate_campaign_hex(None) is None
    assert validate_campaign_hex(CAMPAIGN_HEX_MIN) == CAMPAIGN_HEX_MIN
    assert validate_campaign_hex(CAMPAIGN_HEX_MAX) == CAMPAIGN_HEX_MAX

    with pytest.raises(ValueError, match="Campaign hex"):
        validate_campaign_hex(CAMPAIGN_HEX_MIN - 1)
    with pytest.raises(ValueError, match="Campaign hex"):
        validate_campaign_hex(CAMPAIGN_HEX_MAX + 1)


def test_display_name_falls_back_to_unknown(tmp_path: Path) -> None:
    entry = _add_game(_store(tmp_path), side1=[("11", " \u200b ", "Sons of Horus")])

    assert entry["players"][0]["display_name"] == "Unknown"


def test_games_are_never_pruned(tmp_path: Path) -> None:
    store = _store(tmp_path)

    old = _add_game(
        store,
        club_night_date=date(2020, 1, 1),
        thread_id="999",
        now=datetime(2020, 1, 1, tzinfo=UTC),
    )
    _add_game(store)

    remaining = store.get_games_for_thread(GUILD_ID, "999")
    assert [game["id"] for game in remaining] == [old["id"]]


def test_remove_game_by_reporter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _add_game(store)

    removed = store.remove_game(GUILD_ID, entry["id"], requester_user_id="11")

    assert removed["id"] == entry["id"]
    assert store.get_games_for_thread(GUILD_ID, THREAD_ID) == []


def test_remove_game_by_short_prefix(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _add_game(store)

    removed = store.remove_game(
        GUILD_ID, entry["id"][:8].upper(), requester_user_id="11"
    )

    assert removed["id"] == entry["id"]


def test_remove_game_denied_for_non_reporter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _add_game(store)

    with pytest.raises(GameRemovalNotAllowedError):
        store.remove_game(GUILD_ID, entry["id"], requester_user_id="22")

    assert len(store.get_games_for_thread(GUILD_ID, THREAD_ID)) == 1


def test_remove_game_allow_any_overrides_reporter_check(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _add_game(store)

    removed = store.remove_game(
        GUILD_ID, entry["id"], requester_user_id="99", allow_any=True
    )

    assert removed["id"] == entry["id"]


def test_remove_game_unknown_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add_game(store)

    with pytest.raises(GameNotFoundError):
        store.remove_game(GUILD_ID, "f" * 32, requester_user_id="11")


def test_remove_game_rejects_short_prefix(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _add_game(store)

    with pytest.raises(ValueError, match="at least 8"):
        store.remove_game(GUILD_ID, entry["id"][:4], requester_user_id="11")


def test_remove_game_ambiguous_prefix(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _add_game(store)
    second = _add_game(store)

    path = tmp_path / "state" / "club_night_games.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    shared_prefix = "abcdefab"
    for game in data["games"]:
        game["id"] = shared_prefix + game["id"][8:]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert first["id"][8:] != second["id"][8:]

    with pytest.raises(AmbiguousGameIdError):
        store.remove_game(GUILD_ID, shared_prefix, requester_user_id="11")


def test_remove_game_is_guild_scoped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _add_game(store)

    with pytest.raises(GameNotFoundError):
        store.remove_game(OTHER_GUILD_ID, entry["id"], requester_user_id="11")


def test_get_games_for_thread_filters_thread_and_guild(tmp_path: Path) -> None:
    store = _store(tmp_path)
    here = _add_game(store)
    _add_game(store, thread_id="222")
    _add_game(store, guild_id=OTHER_GUILD_ID)

    games = store.get_games_for_thread(GUILD_ID, THREAD_ID)

    assert [game["id"] for game in games] == [here["id"]]


def test_corrupt_file_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "state" / "club_night_games.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    store = GameStore(path)

    assert store.get_games_for_thread(GUILD_ID, THREAD_ID) == []

    _add_game(store)
    assert len(store.get_games_for_thread(GUILD_ID, THREAD_ID)) == 1


def test_unexpected_shape_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "state" / "club_night_games.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

    assert GameStore(path).get_games_for_thread(GUILD_ID, THREAD_ID) == []


def test_malformed_entries_are_dropped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _add_game(store)

    path = tmp_path / "state" / "club_night_games.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["games"].extend(["bogus", {"id": 42}, {"guild_id": GUILD_ID}])
    path.write_text(json.dumps(data), encoding="utf-8")

    games = store.get_games_for_thread(GUILD_ID, THREAD_ID)
    assert [game["id"] for game in games] == [entry["id"]]


def test_atomic_save_leaves_no_temp_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add_game(store)

    state_dir = tmp_path / "state"
    assert [p.name for p in state_dir.iterdir()] == ["club_night_games.json"]
