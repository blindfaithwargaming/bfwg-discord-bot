from pathlib import Path

import pytest

from bot.services.rules_query import (
    RulesQueryError,
    RulesQueryHit,
    format_rules_hit,
    format_rules_query,
    search_rules,
)


def _write_rule(
    directory: Path,
    slug: str,
    *,
    title: str,
    rules_topic: str = "Key Actions",
    source_book: str | None = None,
    source_section: str = "Key Actions",
    keywords: str,
    body: str,
) -> None:
    book = source_book or directory.name
    (directory / f"{slug}.md").write_text(
        "---\n"
        f"name: {slug}\n"
        f"rules_topic: {rules_topic}\n"
        f"source_book: {book}\n"
        f"source_url: https://wahapedia.ru/heresy3/the-rules/{book}/\n"
        f"source_section: {source_section}\n"
        f"keywords: [{keywords}]\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def _make_rules_dir(tmp_path: Path) -> Path:
    summaries_dir = tmp_path / "summaries"
    rules_dir = summaries_dir / "rules" / "principles-of-combat"
    rules_dir.mkdir(parents=True)
    return summaries_dir


def test_search_rules_rejects_missing_rules_dir(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()

    with pytest.raises(RulesQueryError, match="not available"):
        search_rules(str(summaries_dir), "cover save")


def test_search_rules_rejects_empty_rules_dir(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    (summaries_dir / "rules").mkdir(parents=True)

    with pytest.raises(RulesQueryError, match="empty"):
        search_rules(str(summaries_dir), "cover save")


def test_search_rules_rejects_blank_query(tmp_path: Path) -> None:
    summaries_dir = _make_rules_dir(tmp_path)
    rules_dir = summaries_dir / "rules" / "principles-of-combat"
    _write_rule(
        rules_dir,
        "cover",
        title="Cover",
        keywords="cover, save",
        body="Models in cover gain a bonus to their saving throw.",
    )

    with pytest.raises(RulesQueryError):
        search_rules(str(summaries_dir), "   ")


def test_keyword_frontmatter_bonus_outranks_equal_body_match(tmp_path: Path) -> None:
    summaries_dir = _make_rules_dir(tmp_path)
    rules_dir = summaries_dir / "rules" / "principles-of-combat"

    # Both bodies mention "rending" once. Only one lists it as a keyword.
    _write_rule(
        rules_dir,
        "rending",
        title="Rending",
        keywords="rending, armour, wound",
        body="A weapon with rending can inflict extra damage on a successful hit.",
    )
    _write_rule(
        rules_dir,
        "armour-saves",
        title="Armour Saves",
        keywords="armour, save",
        body="Some attacks such as rending reduce the value of an armour save.",
    )

    hits = search_rules(str(summaries_dir), "rending")

    assert hits[0].path == "principles-of-combat/rending.md"
    assert hits[0].score > hits[1].score


def test_search_rules_excludes_readme(tmp_path: Path) -> None:
    summaries_dir = _make_rules_dir(tmp_path)
    rules_root = summaries_dir / "rules"
    (rules_root / "README.md").write_text(
        "# Rules corpus\nMentions cover and saving throws as an index.",
        encoding="utf-8",
    )
    _write_rule(
        rules_root / "principles-of-combat",
        "cover",
        title="Cover",
        keywords="cover, save",
        body="Models in cover gain a bonus to their saving throw against cover.",
    )

    hits = search_rules(str(summaries_dir), "cover")

    assert [hit.path for hit in hits] == ["principles-of-combat/cover.md"]


def test_format_rules_query_top_hit_includes_citation(tmp_path: Path) -> None:
    summaries_dir = _make_rules_dir(tmp_path)
    _write_rule(
        summaries_dir / "rules" / "principles-of-combat",
        "cover",
        title="Cover and Invulnerable Tests",
        source_section="Key Actions",
        keywords="cover, save, invulnerable",
        body="A model in cover improves its saving throw by one against ranged attacks.",
    )

    hits = search_rules(str(summaries_dir), "cover save")
    message = format_rules_query("cover save", hits)

    assert "**Cover and Invulnerable Tests**" in message
    assert "Source: principles-of-combat — Key Actions" in message
    assert "https://wahapedia.ru/heresy3/the-rules/principles-of-combat/" in message


def test_format_rules_query_keeps_balanced_markdown_in_top_excerpt(tmp_path: Path) -> None:
    summaries_dir = _make_rules_dir(tmp_path)
    _write_rule(
        summaries_dir / "rules" / "principles-of-combat",
        "cover",
        title="Cover and Invulnerable Tests",
        source_section="Key Actions",
        keywords="cover, save, invulnerable",
        body=(
            "**Cover Tests** use a Cover Save granted by terrain or special rules. "
            "Only one Saving Throw may be made."
        ),
    )

    hits = search_rules(str(summaries_dir), "cover save")
    message = format_rules_query("cover save", hits)

    assert "**Cover Tests** use" in message
    assert "\nCover Tests** use" not in message


def test_format_rules_hit_renders_single_result(tmp_path: Path) -> None:
    summaries_dir = _make_rules_dir(tmp_path)
    _write_rule(
        summaries_dir / "rules" / "principles-of-combat",
        "cover",
        title="Cover and Invulnerable Tests",
        source_section="Key Actions",
        keywords="cover, save, invulnerable",
        body="A model in cover improves its saving throw by one against ranged attacks.",
    )

    hits = search_rules(str(summaries_dir), "cover save")
    message = format_rules_hit("cover save", hits[0])

    assert "**Rules query:** cover save" in message
    assert "**Cover and Invulnerable Tests**" in message
    assert "Source: principles-of-combat — Key Actions" in message
    assert "More results:" not in message


def test_format_rules_query_handles_no_matches() -> None:
    message = format_rules_query("titan reactor breach", [])

    assert "titan reactor breach" in message
    assert "still growing" in message


def test_format_rules_query_escapes_public_query_text() -> None:
    message = format_rules_query("@everyone **rending**", [])

    assert "@everyone" not in message
    assert "**rending**" not in message
    assert "\\*\\*rending\\*\\*" in message


def test_format_rules_query_fits_discord_message_limit_many_hits() -> None:
    hits = [
        RulesQueryHit(
            path=f"principles-of-combat/topic_{i:02d}.md",
            title="A Very Long Rules Topic Title " * 5,
            excerpt="a" * 600,
            score=100 - i,
            source_book="principles-of-combat",
            source_section="Advanced Principles — Reactions and Tactical Statuses",
            source_url="https://wahapedia.ru/heresy3/the-rules/principles-of-combat/",
        )
        for i in range(10)
    ]

    assert len(format_rules_query("reactions tactical status", hits)) <= 2000
