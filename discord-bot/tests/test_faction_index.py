from pathlib import Path

import pytest

from bot.services.faction_index import autocomplete_faction, get_faction_names, reset

_REAL_SUMMARIES = Path(__file__).parent.parent.parent / "summaries"


def _write_md(path: Path, topic: str) -> None:
    path.write_text(
        f'---\ntopic: "{topic}"\n---\n# {topic}\n',
        encoding="utf-8",
    )


def setup_function() -> None:
    reset()


# ---------------------------------------------------------------------------
# get_faction_names
# ---------------------------------------------------------------------------


def test_returns_sorted_list(tmp_path):
    _write_md(tmp_path / "sons.md", "Sons of Horus")
    _write_md(tmp_path / "world.md", "World Eaters")
    _write_md(tmp_path / "iron.md", "Iron Warriors")

    names = get_faction_names(str(tmp_path))

    assert names == ["Iron Warriors", "Sons of Horus", "World Eaters"]


def test_deduplicates_same_faction_across_files(tmp_path):
    book1 = tmp_path / "Book_01"
    book1.mkdir()
    book2 = tmp_path / "Book_02"
    book2.mkdir()
    _write_md(book1 / "iron_warriors.md", "Iron Warriors")
    _write_md(book2 / "iron_warriors.md", "Iron Warriors")

    names = get_faction_names(str(tmp_path))

    assert names.count("Iron Warriors") == 1


def test_caches_result(tmp_path):
    _write_md(tmp_path / "sons.md", "Sons of Horus")

    result1 = get_faction_names(str(tmp_path))
    # New file added after first call should not appear (result is cached)
    _write_md(tmp_path / "iron.md", "Iron Warriors")
    result2 = get_faction_names(str(tmp_path))

    assert result1 is result2


def test_missing_dir_returns_empty_list(tmp_path):
    names = get_faction_names(str(tmp_path / "nonexistent"))

    assert names == []


def test_excludes_structural_topics(tmp_path):
    structural = [
        "Overview",
        "Setting and Context",
        "Campaign Narrative",
        "Notable Characters",
        "Key Locations",
        "Vehicles And War Machines",
        "Vehicles and War Machines",
        "Rules Content",
        "Rules Content as Lore",
        "Tone And Themes",
        "Tone and Themes",
    ]
    for i, topic in enumerate(structural):
        _write_md(tmp_path / f"file_{i}.md", topic)

    names = get_faction_names(str(tmp_path))

    assert names == []


def test_excludes_complete_summary_entries(tmp_path):
    _write_md(tmp_path / "book.md", "Betrayal complete summary")

    names = get_faction_names(str(tmp_path))

    assert names == []


def test_excludes_expanded_file_index_entries(tmp_path):
    _write_md(tmp_path / "readme.md", "Betrayal expanded file index")

    names = get_faction_names(str(tmp_path))

    assert names == []


def test_excludes_conversion_progress_entries(tmp_path):
    _write_md(tmp_path / "progress.md", "Retribution dual-summary conversion progress")

    names = get_faction_names(str(tmp_path))

    assert names == []


def test_excludes_thematic_topics(tmp_path):
    thematic = [
        "Daemonic and Ritual War",
        "Red Thirst and Signus Temptation",
        "Word Bearers Betrayal",
        "Traitor Legions and Dark Compliance",
        "Legion Detachments and Loyalist Response",
        "Traitor Hunter Wars",
        "Age of Betrayal and the Shadow Wars",
        "Upon the Anvil of War",
        "The Xana Incursion",
        "Eye of the Storm",
        "Tooth and Claw",
        "The Scouring of the Nostramo Sector",
        "Retribution Campaign and Shadow Wars Missions",
        "Traitor Hunter Forces and Dark Compliance",
        "Calth System Forces",
        "The Shadow Crusade and Calth Trap",
        "Battle for Calth Near-Space",
        "Betrayal on Calth",
        "Macragge's Honour",
        "Battle of Ithraca",
        "The Thramas Crusade",
        "Night Lords Terror War",
        "Dark Angels Counter-Crusade",
        "The Heavy Toll of Victory",
        "Principal Worlds of the Eastern Fringe",
        "Thramas Mortal Forces",
        "Thramas Strategic Context",
        "The Wars of the Shattered Legions",
    ]
    for i, topic in enumerate(thematic):
        _write_md(tmp_path / f"file_{i}.md", topic)

    names = get_faction_names(str(tmp_path))

    assert names == []


def test_normalises_white_scars_compound_topic(tmp_path):
    _write_md(tmp_path / "white_scars.md", "White Scars and Chondax")

    names = get_faction_names(str(tmp_path))

    assert names == ["White Scars"]


def test_normalises_questoris_knights_compound_topic(tmp_path):
    _write_md(tmp_path / "knights.md", "Questoris Knights and Mechanicum")

    names = get_faction_names(str(tmp_path))

    assert names == ["Questoris Knights"]


def test_questoris_knights_and_mechanicum_are_separate_when_both_present(tmp_path):
    _write_md(tmp_path / "knights.md", "Questoris Knights and Mechanicum")
    _write_md(tmp_path / "mechanicum.md", "Mechanicum")

    names = get_faction_names(str(tmp_path))

    assert "Questoris Knights" in names
    assert "Mechanicum" in names


def test_normalises_knights_errant_compound_topic(tmp_path):
    _write_md(tmp_path / "knights_errant.md", "Knights-Errant and Crusader Host")

    names = get_faction_names(str(tmp_path))

    assert names == ["Knights-Errant"]


def test_questoris_knight_households_roll_up_to_umbrella(tmp_path):
    for slug, topic in (
        ("makabius", "House Makabius"),
        ("vyronii", "House Vyronii"),
        ("orhlacc", "House Orhlacc"),
        ("aerthegn", "House Ærthegn"),
        ("vornherr", "House Vornherr"),
    ):
        _write_md(tmp_path / f"{slug}.md", topic)

    names = get_faction_names(str(tmp_path))

    assert names == ["Questoris Knights"]


def test_mechanicum_and_titans_folds_into_mechanicum(tmp_path):
    _write_md(tmp_path / "mechanicum_titans.md", "Mechanicum and Titans")
    _write_md(tmp_path / "mechanicum_taghmata.md", "Mechanicum Taghmata")

    names = get_faction_names(str(tmp_path))

    assert names == ["Mechanicum"]


def test_xana_mechanicum_and_house_malinax_folds_into_mechanicum(tmp_path):
    _write_md(
        tmp_path / "xana_mechanicum.md",
        "Mechanicum of Xana and House Malinax",
    )

    names = get_faction_names(str(tmp_path))

    assert names == ["Mechanicum"]


def test_book9_split_mortal_and_forge_topics_normalise(tmp_path):
    _write_md(
        tmp_path / "nightwatch.md",
        "Thramassi Nightwatch and Solar Auxilia",
    )
    _write_md(
        tmp_path / "triplex.md",
        "Triplex Forge Worlds and Mechanicum",
    )

    names = get_faction_names(str(tmp_path))

    assert names == ["Mechanicum", "Solar Auxilia"]


def test_legion_grouping_topics_are_excluded(tmp_path):
    _write_md(tmp_path / "traitors.md", "Traitor Legions and their Allies")
    _write_md(tmp_path / "loyalists.md", "Loyalist Legions")

    names = get_faction_names(str(tmp_path))

    assert names == []


def test_files_without_frontmatter_are_skipped(tmp_path):
    (tmp_path / "no_frontmatter.md").write_text(
        "# Just a heading\nNo frontmatter here.", encoding="utf-8"
    )

    names = get_faction_names(str(tmp_path))

    assert names == []


def test_utf8_bom_files_are_parsed_correctly(tmp_path):
    # Windows editors often save with a BOM (﻿). Faction files in the real
    # summaries corpus are BOM-encoded, so this must not silently yield zero results.
    content = '---\ntopic: "Sons of Horus"\n---\n# Sons of Horus\n'
    (tmp_path / "sons.md").write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    names = get_faction_names(str(tmp_path))

    assert names == ["Sons of Horus"]


# ---------------------------------------------------------------------------
# autocomplete_faction
# ---------------------------------------------------------------------------


def test_autocomplete_empty_query_returns_up_to_25(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    for i in range(30):
        _write_md(books / f"faction_{i:02d}.md", f"Faction {i:02d}")

    results = autocomplete_faction(str(tmp_path), "")

    assert len(results) == 25


def test_autocomplete_filters_by_substring(tmp_path):
    _write_md(tmp_path / "iron_hands.md", "Iron Hands")
    _write_md(tmp_path / "iron_warriors.md", "Iron Warriors")
    _write_md(tmp_path / "sons.md", "Sons of Horus")

    results = autocomplete_faction(str(tmp_path), "iron")

    assert results == ["Iron Hands", "Iron Warriors"]


def test_autocomplete_case_insensitive(tmp_path):
    _write_md(tmp_path / "sons.md", "Sons of Horus")

    results = autocomplete_faction(str(tmp_path), "SONS")

    assert results == ["Sons of Horus"]


def test_autocomplete_no_match_returns_empty(tmp_path):
    _write_md(tmp_path / "sons.md", "Sons of Horus")

    results = autocomplete_faction(str(tmp_path), "ultramarines")

    assert results == []


def test_autocomplete_strips_whitespace_from_query(tmp_path):
    _write_md(tmp_path / "iron.md", "Iron Warriors")

    results = autocomplete_faction(str(tmp_path), "  iron  ")

    assert results == ["Iron Warriors"]


# ---------------------------------------------------------------------------
# Real summaries integration tests
# ---------------------------------------------------------------------------

_real_summaries_present = pytest.mark.skipif(
    not _REAL_SUMMARIES.exists(),
    reason="summaries directory not present relative to discord-bot",
)

_EXPECTED_FACTIONS = {
    "Sons of Horus",
    "Emperor's Children",
    "World Eaters",
    "Death Guard",
    "Iron Hands",
    "Salamanders",
    "Raven Guard",
    "Night Lords",
    "Iron Warriors",
    "Word Bearers",
    "Alpha Legion",
    "Imperial Fists",
    "Mechanicum",
    "Ultramarines",
    "Space Wolves",
    "Thousand Sons",
    "Blood Angels",
    "White Scars",
    "Dark Angels",
    "Adeptus Custodes",
    "Sisters of Silence",
    "Solar Auxilia",
    "Blackshields",
    "Shattered Legions",
    "Questoris Knights",
    "Knights-Errant",
}


@_real_summaries_present
def test_real_summaries_autocomplete_includes_shattered_legions_by_default() -> None:
    results = autocomplete_faction(str(_REAL_SUMMARIES), "")

    assert "Shattered Legions" in results


@_real_summaries_present
def test_real_summaries_shattered_query_returns_faction_not_feature_topic() -> None:
    results = autocomplete_faction(str(_REAL_SUMMARIES), "shat")

    assert results == ["Shattered Legions"]


@_real_summaries_present
def test_real_summaries_returns_expected_factions() -> None:
    """All core playable factions are present in the index built from real files.

    This test catches encoding issues (e.g. UTF-8 BOM) or regex failures that
    would cause the index to return fewer factions than expected.
    """
    names = get_faction_names(str(_REAL_SUMMARIES))
    missing = _EXPECTED_FACTIONS - set(names)
    assert not missing, f"Missing factions from index: {sorted(missing)}"


@_real_summaries_present
def test_real_summaries_no_structural_topics_leak_through() -> None:
    """Structural topic names must not appear in the faction index."""
    names = set(get_faction_names(str(_REAL_SUMMARIES)))
    structural = {
        "Overview",
        "Setting and Context",
        "Campaign Narrative",
        "Notable Characters",
        "Key Locations",
        "Vehicles And War Machines",
        "Vehicles and War Machines",
        "Rules Content",
        "Rules Content as Lore",
        "Tone And Themes",
        "Tone and Themes",
        "Age of Betrayal and the Shadow Wars",
        "Upon the Anvil of War",
        "The Xana Incursion",
        "Eye of the Storm",
        "Tooth and Claw",
        "The Scouring of the Nostramo Sector",
        "Retribution Campaign and Shadow Wars Missions",
        "Traitor Hunter Forces and Dark Compliance",
        "The Shadow Crusade and Calth Trap",
        "Battle for Calth Near-Space",
        "Betrayal on Calth",
        "Macragge's Honour",
        "Battle of Ithraca",
        "Mechanicum of Xana and House Malinax",
        "Council of Nikaea and the Censure",
        "The Burning of Prospero",
        "The Aether and Psychic Catastrophe",
        "The Thramas Crusade",
        "Night Lords Terror War",
        "Dark Angels Counter-Crusade",
        "The Heavy Toll of Victory",
        "Principal Worlds of the Eastern Fringe",
    }
    leaked = structural & names
    assert not leaked, f"Structural topics leaked into faction index: {leaked}"
