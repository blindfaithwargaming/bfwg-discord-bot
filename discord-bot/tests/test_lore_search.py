from pathlib import Path

import pytest

from bot.services.lore_search import (
    LoreSearchError,
    LoreSearchHit,
    format_lore_search,
    format_source_results,
    search_lore,
)


def test_search_lore_returns_ranked_excerpts_with_source_paths(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    black_books_dir = summaries_dir / "Heresy Black Books"
    black_books_dir.mkdir(parents=True)
    (black_books_dir / "imperial_fists.md").write_text(
        "# Imperial Fists\n"
        "The Imperial Fists are masters of siegecraft, void war, and disciplined defence. "
        "Imperial Fists commanders prize fortification and stubborn resolve.",
        encoding="utf-8",
    )
    (black_books_dir / "iron_warriors.md").write_text(
        "# Iron Warriors\n"
        "The Iron Warriors prosecute siege warfare through attrition and brutal artillery.",
        encoding="utf-8",
    )

    hits = search_lore(str(summaries_dir), "Imperial Fists siege")

    assert [hit.path for hit in hits] == [
        "Heresy Black Books/imperial_fists.md",
        "Heresy Black Books/iron_warriors.md",
    ]
    assert "Imperial Fists" in hits[0].excerpt
    assert hits[0].score > hits[1].score


def test_search_lore_prefers_content_files_over_readme(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "README.md").write_text(
        "# Index\n"
        "Night Lords appear in this index, but source lookup should prefer content files.",
        encoding="utf-8",
    )
    (summaries_dir / "night_lords.md").write_text(
        "The Night Lords weaponise terror, sudden violence, and atrocity as strategic tools.",
        encoding="utf-8",
    )

    hits = search_lore(str(summaries_dir), "Night Lords")

    assert [hit.path for hit in hits] == ["night_lords.md"]


def test_search_lore_includes_rules_but_excludes_missions(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    black_books_dir = summaries_dir / "Heresy Black Books"
    black_books_dir.mkdir(parents=True)
    (black_books_dir / "night_lords.md").write_text(
        "The Night Lords weaponise terror through a sudden breaching assault.",
        encoding="utf-8",
    )
    rules_dir = summaries_dir / "rules" / "armoury-of-the-age-of-darkness"
    rules_dir.mkdir(parents=True)
    (rules_dir / "breaching.md").write_text(
        "# Breaching\nBreaching attacks turn a wound into one that uses AP 2.",
        encoding="utf-8",
    )
    missions_dir = summaries_dir / "missions" / "primary-objectives"
    missions_dir.mkdir(parents=True)
    (missions_dir / "breaching_run.md").write_text(
        "# Breaching Run\nScore points for each breaching objective held.",
        encoding="utf-8",
    )

    hits = search_lore(str(summaries_dir), "breaching")

    assert [hit.path for hit in hits] == [
        "rules/armoury-of-the-age-of-darkness/breaching.md",
        "Heresy Black Books/night_lords.md",
    ]


def test_search_lore_hides_agent_summaries_and_serves_public_summaries(
    tmp_path: Path,
) -> None:
    summaries_dir = tmp_path / "summaries"
    book_dir = summaries_dir / "Heresy Black Books" / "Book_01_Betrayal"
    agent_dir = book_dir / "agent"
    public_dir = book_dir / "public"
    agent_dir.mkdir(parents=True)
    public_dir.mkdir(parents=True)
    (agent_dir / "03_the_lodge_system.md").write_text(
        "Agent-only lodge context includes extraction notes and generation constraints.",
        encoding="utf-8",
    )
    (public_dir / "03_the_lodge_system.md").write_text(
        "The warrior lodges turn private brotherhood into a hidden channel for betrayal.",
        encoding="utf-8",
    )

    hits = search_lore(str(summaries_dir), "lodge betrayal", limit=10)

    assert [hit.path for hit in hits] == [
        "Heresy Black Books/Book_01_Betrayal/public/03_the_lodge_system.md"
    ]


def test_search_lore_allows_no_matches(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text(
        "The Thramas Crusade became a war of pursuit and reprisal.",
        encoding="utf-8",
    )

    assert search_lore(str(summaries_dir), "Mechanicum") == []


def test_search_lore_rejects_missing_summaries_dir(tmp_path: Path) -> None:
    with pytest.raises(LoreSearchError, match="does not exist"):
        search_lore(str(tmp_path / "missing"), "Imperial Fists")


def test_search_lore_rejects_empty_query(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text("Imperial Fists", encoding="utf-8")

    with pytest.raises(LoreSearchError, match="search query"):
        search_lore(str(summaries_dir), " ")


def test_format_lore_search_handles_no_matches() -> None:
    message = format_lore_search("Solar Auxilia", [])

    assert "Solar Auxilia" in message
    assert "No matching summaries found" in message


def test_format_lore_search_escapes_public_query_text() -> None:
    message = format_lore_search("@everyone **Solar Auxilia**", [])

    assert "@everyone" not in message
    assert "**Solar Auxilia**" not in message
    assert "\\*\\*Solar Auxilia\\*\\*" in message


def test_format_source_results_lists_sources() -> None:
    message = format_source_results(
        "Calth",
        [
            LoreSearchHit(
                path="Heresy Black Books/Book_05_Tempest/01_overview.md",
                excerpt="Calth becomes the defining betrayal in the Underworld War.",
                score=10,
            )
        ],
    )

    assert "`Heresy Black Books/Book_05_Tempest/01_overview.md`" in message
    assert "Calth becomes" in message


def test_search_lore_rejects_directory_with_no_markdown_files(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "notes.txt").write_text("Some text", encoding="utf-8")

    with pytest.raises(LoreSearchError, match="No Markdown summaries found"):
        search_lore(str(summaries_dir), "Imperial Fists")


def test_format_source_results_handles_no_matches() -> None:
    message = format_source_results("Calth", [])

    assert "Calth" in message
    assert "No matching summary files found" in message


def test_format_lore_search_fits_discord_message_limit(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text(
        "Sons of Horus " + ("advance through a ruined compliance zone " * 200) + ".",
        encoding="utf-8",
    )

    hits = search_lore(str(summaries_dir), "Sons of Horus", limit=10)

    assert len(format_lore_search("Sons of Horus", hits)) <= 2000


def test_format_lore_search_fits_discord_message_limit_many_hits() -> None:
    hits = [
        LoreSearchHit(
            path=f"Heresy Black Books/Book_{i:02d}_Long_Campaign_Title/master_summary.md",
            excerpt="a" * 260,
            score=10 - i,
        )
        for i in range(10)
    ]

    assert len(format_lore_search("iron warriors siege", hits)) <= 2000


def test_format_source_results_fits_discord_message_limit() -> None:
    hits = [
        LoreSearchHit(
            path=f"Heresy Black Books/Book_{i:02d}_Long_Campaign_Title/master_summary.md",
            excerpt="a" * 260,
            score=10 - i,
        )
        for i in range(10)
    ]

    assert len(format_source_results("iron warriors siege", hits)) <= 2000
