"""Integrity checks for the real summaries/rules/ corpus.

Web-sourced (Wahapedia) so files cite source_url + source_section instead of page
numbers. With zero corpus files the directory-existence assertion still runs, so the
scaffold itself is checked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REAL_RULES = Path(__file__).parent.parent.parent / "summaries" / "rules"

_REQUIRED_KEYS = ("name", "rules_topic", "source_book", "source_url", "source_section", "keywords")

_real_rules_present = pytest.mark.skipif(
    not _REAL_RULES.exists(),
    reason="summaries/rules directory not present relative to discord-bot",
)


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    frontmatter_text = text[4:end].strip()
    body = text[end + 4 :].lstrip("\n")
    frontmatter: dict[str, str] = {}
    for line in frontmatter_text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = value.strip()
    return frontmatter, body


def _parse_list(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [item.strip() for item in value.split(",") if item.strip()]


def _corpus_files() -> list[Path]:
    return [
        path
        for path in _REAL_RULES.rglob("*.md")
        if path.name.lower() != "readme.md"
    ]


@_real_rules_present
def test_rules_files_meet_schema() -> None:
    for path in _corpus_files():
        text = path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(text)

        for key in _REQUIRED_KEYS:
            assert frontmatter.get(key), f"{path.name}: missing frontmatter key {key!r}"

        assert "topic" not in frontmatter, (
            f"{path.name}: uses forbidden 'topic:' key — must be 'rules_topic:' "
            "(a 'topic:' key pollutes faction autocomplete)"
        )

        assert frontmatter["source_book"] == path.parent.name, (
            f"{path.name}: source_book {frontmatter['source_book']!r} "
            f"does not match parent directory {path.parent.name!r}"
        )

        assert frontmatter["source_url"].startswith("https://"), (
            f"{path.name}: source_url must be an https:// URL"
        )

        keywords = _parse_list(frontmatter["keywords"])
        assert keywords, f"{path.name}: keywords list is empty"
        for kw in keywords:
            assert kw == kw.lower(), f"{path.name}: keyword {kw!r} is not lowercase"

        h1_lines = [line for line in body.splitlines() if line.startswith("# ")]
        assert len(h1_lines) == 1, f"{path.name}: expected exactly one '# ' H1, got {len(h1_lines)}"

        assert len(body) <= 1500, (
            f"{path.name}: body is {len(body)} chars (limit 1500) — split into two files"
        )
