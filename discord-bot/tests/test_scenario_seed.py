from pathlib import Path

import pytest

from bot.services.scenario_seed import (
    COMPLICATIONS,
    ScenarioSeed,
    ScenarioSeedError,
    TONE_MODIFIERS,
    format_scenario_seed,
    generate_scenario_seed,
    generate_scenario_twist,
)


def test_generate_scenario_seed_matches_attacker_and_defender_sources(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    black_books_dir = summaries_dir / "Heresy Black Books"
    black_books_dir.mkdir(parents=True)
    (black_books_dir / "sons_of_horus.md").write_text(
        "# Sons of Horus\n"
        "The Sons of Horus prosecute shock assaults with brutal speed and command precision.",
        encoding="utf-8",
    )
    (black_books_dir / "imperial_fists.md").write_text(
        "# Imperial Fists\n"
        "The Imperial Fists are defined by siegecraft, void war, and disciplined defence.",
        encoding="utf-8",
    )

    seed = generate_scenario_seed(
        str(summaries_dir),
        "Sons of Horus",
        "Imperial Fists",
        "siege",
    )

    assert seed.attacker == "Sons of Horus"
    assert seed.defender == "Imperial Fists"
    assert seed.tone == "siege"
    assert [source.path for source in seed.sources] == [
        "Heresy Black Books/sons_of_horus.md",
        "Heresy Black Books/imperial_fists.md",
    ]
    assert "Sons of Horus" in seed.sources[0].excerpt
    assert "Imperial Fists" in seed.sources[1].excerpt


def test_generate_scenario_seed_is_deterministic(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text(
        "Alpha Legion operatives stalk White Scars outriders through a broken supply corridor.",
        encoding="utf-8",
    )

    first = generate_scenario_seed(str(summaries_dir), "Alpha Legion", "White Scars", "traitor")
    second = generate_scenario_seed(str(summaries_dir), "Alpha Legion", "White Scars", "traitor")

    assert first == second


def test_generate_scenario_seed_allows_unmatched_factions(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text(
        "The Thramas Crusade ground on through ambush, counter-ambush, and bitter pursuit.",
        encoding="utf-8",
    )

    seed = generate_scenario_seed(str(summaries_dir), "Unknown Raiders", "Mystery Defenders")
    message = format_scenario_seed(seed)

    assert seed.sources == []
    assert "No direct summary match" in message


def test_generate_scenario_seed_rejects_missing_summaries_dir(tmp_path: Path) -> None:
    with pytest.raises(ScenarioSeedError, match="does not exist"):
        generate_scenario_seed(str(tmp_path / "missing"), "Sons of Horus", "Imperial Fists")


def test_generate_scenario_seed_rejects_empty_attacker(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text("Imperial Fists", encoding="utf-8")

    with pytest.raises(ScenarioSeedError, match="attacker"):
        generate_scenario_seed(str(summaries_dir), " ", "Imperial Fists")


def test_generate_scenario_seed_rejects_empty_defender(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text("Sons of Horus", encoding="utf-8")

    with pytest.raises(ScenarioSeedError, match="defender"):
        generate_scenario_seed(str(summaries_dir), "Sons of Horus", " ")


def test_generate_scenario_seed_rejects_directory_with_no_markdown_files(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()

    with pytest.raises(ScenarioSeedError, match="No Markdown summaries found"):
        generate_scenario_seed(str(summaries_dir), "Sons of Horus", "Imperial Fists")


def test_format_scenario_seed_fits_discord_message_limit(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text("Sons of Horus Imperial Fists " * 300, encoding="utf-8")

    seed = generate_scenario_seed(str(summaries_dir), "Sons of Horus", "Imperial Fists")

    assert len(format_scenario_seed(seed)) <= 2000


def test_generate_scenario_seed_reroll_varies_result(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text(
        "Sons of Horus Imperial Fists siege warfare Isstvan.",
        encoding="utf-8",
    )

    first = generate_scenario_seed(str(summaries_dir), "Sons of Horus", "Imperial Fists", reroll=0)
    second = generate_scenario_seed(str(summaries_dir), "Sons of Horus", "Imperial Fists", reroll=1)

    assert first != second


def test_generate_scenario_seed_reroll_is_deterministic(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text(
        "Sons of Horus Imperial Fists siege warfare Isstvan.",
        encoding="utf-8",
    )

    first = generate_scenario_seed(str(summaries_dir), "Sons of Horus", "Imperial Fists", reroll=3)
    second = generate_scenario_seed(str(summaries_dir), "Sons of Horus", "Imperial Fists", reroll=3)

    assert first == second


# --- mission_type / theatre overrides ---


def test_generate_scenario_seed_uses_fixed_mission_type(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text("Sons of Horus Imperial Fists", encoding="utf-8")

    seed = generate_scenario_seed(
        str(summaries_dir),
        "Sons of Horus",
        "Imperial Fists",
        mission_type="tunnel warfare",
    )

    assert "tunnel warfare" in seed.briefing


def test_generate_scenario_seed_uses_fixed_theatre(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text("Sons of Horus Imperial Fists", encoding="utf-8")

    seed = generate_scenario_seed(
        str(summaries_dir),
        "Sons of Horus",
        "Imperial Fists",
        theatre="a flooded underhive",
    )

    assert "a flooded underhive" in seed.briefing


def test_generate_scenario_seed_fixed_params_are_deterministic(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text("Sons of Horus Imperial Fists", encoding="utf-8")

    first = generate_scenario_seed(
        str(summaries_dir),
        "Sons of Horus",
        "Imperial Fists",
        mission_type="tunnel warfare",
        theatre="a flooded underhive",
    )
    second = generate_scenario_seed(
        str(summaries_dir),
        "Sons of Horus",
        "Imperial Fists",
        mission_type="tunnel warfare",
        theatre="a flooded underhive",
    )

    assert first == second


def test_generate_scenario_seed_fixed_mission_type_preserved_across_rerolls(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text("Sons of Horus Imperial Fists", encoding="utf-8")

    first = generate_scenario_seed(
        str(summaries_dir), "Sons of Horus", "Imperial Fists", mission_type="tunnel warfare", reroll=0
    )
    second = generate_scenario_seed(
        str(summaries_dir), "Sons of Horus", "Imperial Fists", mission_type="tunnel warfare", reroll=1
    )

    assert "tunnel warfare" in first.briefing
    assert "tunnel warfare" in second.briefing
    assert first != second


def test_generate_scenario_seed_rejects_whitespace_only_mission_type(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text("Sons of Horus", encoding="utf-8")

    with pytest.raises(ScenarioSeedError, match="mission type"):
        generate_scenario_seed(str(summaries_dir), "Sons of Horus", "Imperial Fists", mission_type="   ")


def test_generate_scenario_seed_rejects_whitespace_only_theatre(tmp_path: Path) -> None:
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "source.md").write_text("Sons of Horus", encoding="utf-8")

    with pytest.raises(ScenarioSeedError, match="theatre"):
        generate_scenario_seed(str(summaries_dir), "Sons of Horus", "Imperial Fists", theatre="   ")


# --- generate_scenario_twist ---


def test_generate_scenario_twist_returns_known_complication() -> None:
    result = generate_scenario_twist()
    assert result in COMPLICATIONS


def test_generate_scenario_twist_with_known_tone_includes_modifier() -> None:
    result = generate_scenario_twist(tone="grim")
    modifier = TONE_MODIFIERS["grim"]
    assert result.endswith(modifier)
    base = result[: -len(modifier)].rstrip()
    assert base in COMPLICATIONS


def test_generate_scenario_twist_with_unknown_tone_returns_plain_complication() -> None:
    result = generate_scenario_twist(tone="unknown-tone-xyz")
    assert result in COMPLICATIONS


def test_generate_scenario_twist_with_no_tone_returns_plain_complication() -> None:
    result = generate_scenario_twist(None)
    assert result in COMPLICATIONS


def test_generate_scenario_twist_tone_case_insensitive() -> None:
    result = generate_scenario_twist(tone="GRIM")
    modifier = TONE_MODIFIERS["grim"]
    assert result.endswith(modifier)


def test_format_scenario_seed_escapes_public_user_text() -> None:
    seed = ScenarioSeed(
        title="@everyone **Ambush**",
        attacker="@everyone **Alpha Legion**",
        defender="@here _Imperial Fists_",
        tone="grim **siege**",
        briefing="@everyone commit to a **raid** against @here.",
        attacker_objective="seize the command node",
        defender_objective="hold the extraction route",
        complication="Night fighting makes target priority unreliable.",
        sources=[],
    )

    message = format_scenario_seed(seed)

    assert "@everyone" not in message
    assert "@here" not in message
    assert "**Alpha Legion**" not in message
    assert "_Imperial Fists_" not in message
    assert "\\*\\*Alpha Legion\\*\\*" in message
