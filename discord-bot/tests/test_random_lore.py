import random
import sys
from pathlib import Path

import pytest

from bot.services.lore_search import search_lore
from bot.services.random_lore import (
    RandomLore,
    RandomLoreError,
    format_random_lore,
    generate_random_lore,
)


class _PickLastRandom:
    def choice(self, values):
        return values[-1]


def test_generate_random_lore_can_focus_on_a_faction(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    black_books_dir = summaries_dir / "Heresy Black Books"
    black_books_dir.mkdir(parents=True)
    (black_books_dir / "imperial_fists.md").write_text(
        "# Imperial Fists\n"
        "The Imperial Fists answer sieges with disciplined defence, void war expertise, "
        "and a stubborn refusal to yield vital ground.",
        encoding="utf-8",
    )
    (black_books_dir / "night_lords.md").write_text(
        "# Night Lords\n"
        "The Night Lords turn terror, speed, and atrocity into operational weapons.",
        encoding="utf-8",
    )

    lore = generate_random_lore(
        str(summaries_dir),
        faction="Imperial Fists",
        rng=random.Random(1),
    )

    assert lore.focus == "Imperial Fists"
    assert lore.source_path == "Heresy Black Books/imperial_fists.md"
    assert "Imperial Fists" in lore.excerpt
    assert lore.campaign_prompt
    assert lore.table_hook


def test_generate_random_lore_uses_general_archive_without_focus(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "calth.md").write_text(
        "The Calth campaign collapsed from ordered muster into betrayal, underground war, "
        "and desperate survival beneath a poisoned sun.",
        encoding="utf-8",
    )

    lore = generate_random_lore(str(summaries_dir), rng=random.Random(2))

    assert lore.focus == "General archive"
    assert lore.source_path == "calth.md"
    assert "Calth campaign" in lore.excerpt


def test_generate_random_lore_prefers_content_files_over_readme(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "README.md").write_text(
        "# Summary Index\n"
        "Imperial Fists appear in this index, but the actual summary file should be preferred.",
        encoding="utf-8",
    )
    (summaries_dir / "imperial_fists.md").write_text(
        "The Imperial Fists hold the line through disciplined fortification, void command, "
        "and absolute refusal to abandon the walls.",
        encoding="utf-8",
    )

    lore = generate_random_lore(
        str(summaries_dir),
        faction="Imperial Fists",
        rng=random.Random(1),
    )

    assert lore.source_path == "imperial_fists.md"


def test_generate_random_lore_excludes_rules_but_search_includes_them(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "calth.md").write_text(
        "The Calth campaign became a bitter underground war after betrayal shattered "
        "the mustering fleet.",
        encoding="utf-8",
    )
    rules_dir = summaries_dir / "rules" / "armoury-of-the-age-of-darkness"
    rules_dir.mkdir(parents=True)
    (rules_dir / "breaching.md").write_text(
        "# Breaching\n"
        "Breaching attacks can turn a successful wound into one that uses AP 2.",
        encoding="utf-8",
    )

    lore = generate_random_lore(str(summaries_dir), rng=_PickLastRandom())

    assert lore.source_path == "calth.md"
    assert [hit.path for hit in search_lore(str(summaries_dir), "breaching")] == [
        "rules/armoury-of-the-age-of-darkness/breaching.md"
    ]


def test_generate_random_lore_and_search_both_exclude_missions(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "calth.md").write_text(
        "The Calth campaign became a bitter underground war after betrayal shattered "
        "the mustering fleet.",
        encoding="utf-8",
    )
    missions_dir = summaries_dir / "missions" / "primary-objectives"
    missions_dir.mkdir(parents=True)
    (missions_dir / "blood_and_zeal.md").write_text(
        "# Blood and Zeal\n"
        "Score victory points for each objective held at the end of a battle round.",
        encoding="utf-8",
    )

    lore = generate_random_lore(str(summaries_dir), rng=_PickLastRandom())

    assert lore.source_path == "calth.md"
    assert search_lore(str(summaries_dir), "zeal") == []


def test_generate_random_lore_hides_agent_summaries_and_serves_public_summaries(
    tmp_path: Path,
) -> None:
    summaries_dir = tmp_path / "summaries"
    book_dir = summaries_dir / "Heresy Black Books" / "Book_01_Betrayal"
    agent_dir = book_dir / "agent"
    public_dir = book_dir / "public"
    agent_dir.mkdir(parents=True)
    public_dir.mkdir(parents=True)
    (agent_dir / "04_sons_of_horus.md").write_text(
        "Agent-only Sons of Horus extraction notes for future generated lore.",
        encoding="utf-8",
    )
    (public_dir / "04_sons_of_horus.md").write_text(
        "The Sons of Horus prosecute wars through sudden decapitation strikes.",
        encoding="utf-8",
    )

    lore = generate_random_lore(
        str(summaries_dir),
        faction="Sons of Horus",
        rng=random.Random(1),
    )

    assert lore.source_path == (
        "Heresy Black Books/Book_01_Betrayal/public/04_sons_of_horus.md"
    )
    assert "decapitation strikes" in lore.excerpt


def test_generate_random_lore_rejects_missing_summaries_dir(tmp_path: Path) -> None:
    with pytest.raises(RandomLoreError, match="does not exist"):
        generate_random_lore(str(tmp_path / "missing"), rng=random.Random(1))


def test_generate_random_lore_rejects_unmatched_focus(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text(
        "The Thramas Crusade became a long war of ambush, pursuit, and reprisal.",
        encoding="utf-8",
    )

    with pytest.raises(RandomLoreError, match="No Markdown summaries matched"):
        generate_random_lore(str(summaries_dir), faction="Solar Auxilia", rng=random.Random(1))


def test_format_random_lore_fits_discord_message_limit(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text(
        "Sons of Horus " + ("advance through a ruined compliance zone " * 150) + ".",
        encoding="utf-8",
    )

    lore = generate_random_lore(str(summaries_dir), faction="Sons of Horus", rng=random.Random(1))

    assert len(format_random_lore(lore)) <= 2000


@pytest.mark.skipif(sys.platform == "win32", reason="chmod has no effect on Windows")
def test_generate_random_lore_skips_unreadable_files(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    good_file = summaries_dir / "iron_warriors.md"
    good_file.write_text(
        "The Iron Warriors prosecute sieges with methodical attrition and overwhelming firepower.",
        encoding="utf-8",
    )
    bad_file = summaries_dir / "corrupted.md"
    bad_file.write_text("Iron Warriors ghost data.", encoding="utf-8")
    bad_file.chmod(0o000)

    try:
        lore = generate_random_lore(
            str(summaries_dir),
            faction="Iron Warriors",
            rng=random.Random(1),
        )
        assert lore.source_path == "iron_warriors.md"
    finally:
        bad_file.chmod(0o644)


def test_generate_random_lore_matches_tokens_in_filename(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "alpha_legion.md").write_text(
        "Operatives move unseen through civilian infrastructure before a campaign begins.",
        encoding="utf-8",
    )

    lore = generate_random_lore(
        str(summaries_dir),
        faction="alpha legion",
        rng=random.Random(1),
    )

    assert lore.source_path == "alpha_legion.md"


def test_format_random_lore_escapes_public_focus_and_title() -> None:
    lore = RandomLore(
        title="Archive Fragment: @everyone **Alpha Legion**",
        focus="@here **Alpha Legion**",
        source_path="source.md",
        excerpt="Alpha Legion operatives compromise the signal chain.",
        campaign_prompt="Use this as the opening incident.",
        table_hook="Place a compromised vox-node near the centre.",
    )

    message = format_random_lore(lore)

    assert "@everyone" not in message
    assert "@here" not in message
    assert "**Alpha Legion**" not in message
    assert "\\*\\*Alpha Legion\\*\\*" in message
