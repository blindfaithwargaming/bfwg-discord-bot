import json
from pathlib import Path

import pytest

from bot.services.name_generator import (
    CATEGORIES,
    FACTION_NAMES,
    NameGeneratorError,
    _DATA_DIR,
    _faction_to_filename,
    format_names,
    generate_names,
    reset,
)
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT

_ALL_FACTIONS = list(FACTION_NAMES)

_REQUIRED_STYLE_KEYS = frozenset({
    "given", "titles", "epithets", "adj", "nouns",
    "collective", "setting", "place_types",
})


def setup_function() -> None:
    reset()


# ---------------------------------------------------------------------------
# generate_names — basic correctness
# ---------------------------------------------------------------------------


def test_returns_requested_count():
    names = generate_names("Space Wolves", "commander", count=5, seed=1)
    assert len(names) == 5


def test_returns_strings():
    names = generate_names("Ultramarines", "ship", count=3, seed=42)
    assert all(isinstance(n, str) for n in names)


def test_names_are_non_empty():
    names = generate_names("Iron Warriors", "warband", count=4, seed=99)
    assert all(n.strip() for n in names)


def test_count_of_one():
    names = generate_names("Blood Angels", "relic", count=1, seed=7)
    assert len(names) == 1


def test_count_of_zero_returns_empty():
    names = generate_names("Space Wolves", "commander", count=0, seed=1)
    assert names == []


# ---------------------------------------------------------------------------
# generate_names — determinism and seeds
# ---------------------------------------------------------------------------


def test_same_seed_is_deterministic():
    a = generate_names("Space Wolves", "commander", count=5, seed=123)
    b = generate_names("Space Wolves", "commander", count=5, seed=123)
    assert a == b


def test_different_seeds_give_different_names():
    a = generate_names("Thousand Sons", "ship", count=5, seed=1)
    b = generate_names("Thousand Sons", "ship", count=5, seed=2)
    assert a != b


def test_no_seed_produces_list():
    names = generate_names("Death Guard", "location", count=3)
    assert len(names) == 3


# ---------------------------------------------------------------------------
# generate_names — uniqueness
# ---------------------------------------------------------------------------


def test_names_are_unique():
    names = generate_names("Night Lords", "warband", count=5, seed=55)
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# generate_names — error handling
# ---------------------------------------------------------------------------


def test_unknown_faction_raises():
    with pytest.raises(NameGeneratorError, match="Unknown faction"):
        generate_names("Made Up Faction", "commander", seed=1)


def test_unknown_category_raises():
    with pytest.raises(NameGeneratorError, match="Unknown category"):
        generate_names("Space Wolves", "planet", seed=1)


def test_case_insensitive_faction_lookup():
    names = generate_names("space wolves", "commander", count=3, seed=1)
    assert len(names) == 3


def test_faction_with_leading_trailing_whitespace():
    names = generate_names("  Iron Warriors  ", "ship", count=2, seed=1)
    assert len(names) == 2


# ---------------------------------------------------------------------------
# generate_names — all factions and categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("faction", _ALL_FACTIONS)
def test_all_factions_produce_names(faction):
    names = generate_names(faction, "commander", count=3, seed=42)
    assert len(names) == 3
    assert all(isinstance(n, str) and n.strip() for n in names)


@pytest.mark.parametrize("category", CATEGORIES)
def test_all_categories_produce_names(category):
    names = generate_names("Space Wolves", category, count=3, seed=42)
    assert len(names) == 3
    assert all(isinstance(n, str) and n.strip() for n in names)


# ---------------------------------------------------------------------------
# JSON data file integrity
# ---------------------------------------------------------------------------


def test_all_faction_json_files_exist():
    missing = [f for f in FACTION_NAMES if not (_DATA_DIR / _faction_to_filename(f)).exists()]
    assert not missing, f"Missing JSON files for factions: {missing}"


def test_all_json_files_have_required_keys():
    for faction in FACTION_NAMES:
        path = _DATA_DIR / _faction_to_filename(faction)
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = _REQUIRED_STYLE_KEYS - data.keys()
        assert not missing, f"JSON for {faction!r} is missing keys: {missing}"


def test_all_json_values_are_non_empty_lists():
    for faction in FACTION_NAMES:
        path = _DATA_DIR / _faction_to_filename(faction)
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in _REQUIRED_STYLE_KEYS:
            assert isinstance(data[key], list) and data[key], (
                f"JSON for {faction!r}: key {key!r} must be a non-empty list"
            )


def test_faction_to_filename_produces_expected_names():
    assert _faction_to_filename("Space Wolves") == "space_wolves.json"
    assert _faction_to_filename("Emperor's Children") == "emperors_children.json"
    assert _faction_to_filename("Knights-Errant") == "knights_errant.json"
    assert _faction_to_filename("Sons of Horus") == "sons_of_horus.json"


# ---------------------------------------------------------------------------
# format_names
# ---------------------------------------------------------------------------


def test_format_contains_faction():
    result = format_names("Space Wolves", "commander", ["Wolf Lord Bjorngar"])
    assert "Space Wolves" in result


def test_format_contains_category():
    result = format_names("Space Wolves", "commander", ["Wolf Lord Bjorngar"])
    assert "Commander" in result


def test_format_lists_all_names():
    names = ["Name One", "Name Two", "Name Three"]
    result = format_names("Ultramarines", "ship", names)
    for name in names:
        assert name in result


def test_format_uses_bullet_points():
    result = format_names("Iron Warriors", "warband", ["Test Name"])
    assert "• Test Name" in result


def test_format_within_discord_limit():
    long_names = [f"Very Long Name {i} " * 5 for i in range(5)]
    result = format_names("Space Wolves", "commander", long_names)
    assert len(result) <= DISCORD_MESSAGE_LIMIT


def test_format_empty_names_list():
    result = format_names("Space Wolves", "commander", [])
    assert "Space Wolves" in result
    assert "Commander" in result


# ---------------------------------------------------------------------------
# Smoke test: all factions × all categories generate without error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("faction", _ALL_FACTIONS)
@pytest.mark.parametrize("category", CATEGORIES)
def test_all_combinations_smoke(faction, category):
    names = generate_names(faction, category, count=2, seed=0)
    assert len(names) == 2
