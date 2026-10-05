import random
from pathlib import Path

import pytest

from bot.services import mission_generator
from bot.services.mission_generator import (
    MissionEntry,
    MissionGeneratorError,
    MissionResult,
    SecondaryResult,
    _load_entries,
    _objective_placement_text,
    _scoring_text,
    format_mission,
    generate_mission,
)

_REAL_SUMMARIES = Path(__file__).parent.parent.parent / "summaries"


def _write_deployment(directory: Path, slug: str, title: str, editions: list[str]) -> None:
    editions_str = ", ".join(editions)
    (directory / f"{slug}.md").write_text(
        f"---\nname: {slug}\nsource_editions: [{editions_str}]\ntitan_mission: false\npoints_minimum: null\nfaction_tags: []\n---\n\n# {title}\n\nDeploy within 12\" of your long table edge.\n\n**Standard 6'×4' table:** 12\"×48\" per side.\n",
        encoding="utf-8",
    )


def _write_primary(directory: Path, slug: str, title: str, titan: bool = False) -> None:
    titan_str = "true" if titan else "false"
    (directory / f"{slug}.md").write_text(
        f"---\nname: {slug}\nsource_editions: [heresy3]\ntitan_mission: {titan_str}\npoints_minimum: null\nfaction_tags: []\n---\n\n# {title}\n\nOne central objective worth 3VP.\n",
        encoding="utf-8",
    )


def _write_secondary(
    directory: Path,
    slug: str,
    title: str,
    points_minimum: int | None = None,
) -> None:
    pm_str = str(points_minimum) if points_minimum is not None else "null"
    (directory / f"{slug}.md").write_text(
        f"---\nname: {slug}\nsource_editions: [heresy3]\ntitan_mission: false\npoints_minimum: {pm_str}\nfaction_tags: []\n---\n\n# {title}\n\nRemove the enemy warlord to score.\n",
        encoding="utf-8",
    )


def _write_twist(directory: Path, slug: str, title: str) -> None:
    (directory / f"{slug}.md").write_text(
        f"---\nname: {slug}\ncategory: environmental\npoints_minimum: null\nfaction_tags: []\n---\n\n# {title}\n\nAll units suffer -1 to hit at range.\n\n## Narrative Hook\nDust and debris obscure the battlefield.\n",
        encoding="utf-8",
    )


def _build_missions_dir(tmp_path: Path) -> Path:
    root = tmp_path / "summaries"
    missions = root / "missions"

    deployments = missions / "deployment-zones"
    deployments.mkdir(parents=True)
    _write_deployment(deployments, "dawn-of-war", "Dawn of War", ["heresy3", "heresy2"])

    primaries = missions / "primary-objectives"
    primaries.mkdir(parents=True)
    _write_primary(primaries, "the-heart-of-battle", "The Heart of Battle")

    secondaries = missions / "secondary-objectives"
    secondaries.mkdir(parents=True)
    _write_secondary(secondaries, "slay-the-warlord", "Slay the Warlord", 3000)
    _write_secondary(secondaries, "last-man-standing", "Last Man Standing")

    return str(root)


def test_generate_mission_returns_valid_result(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)

    result = generate_mission(summaries_dir)

    assert result.primary.title == "The Heart of Battle"
    assert result.deployment.title == "Dawn of War"
    assert len(result.secondaries) == 2
    assert result.twist is None


def test_generate_mission_seeded_rng_is_deterministic(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)

    first = generate_mission(summaries_dir, rng=random.Random(7))
    second = generate_mission(summaries_dir, rng=random.Random(7))

    assert first == second


def test_generate_mission_seeded_rng_can_vary_results(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)

    point_sets = {
        tuple(secondary.points for secondary in generate_mission(
            summaries_dir,
            rng=random.Random(seed),
        ).secondaries)
        for seed in range(10)
    }

    assert len(point_sets) > 1
    assert all(1 <= point <= 4 for point_set in point_sets for point in point_set)


def test_generate_mission_includes_twist_when_requested(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)
    twists = Path(summaries_dir) / "missions" / "twists"
    twists.mkdir(parents=True)
    _write_twist(twists, "ash-fall", "Ash Fall")

    result = generate_mission(summaries_dir, include_twist=True)

    assert result.twist is not None
    assert result.twist.title == "Ash Fall"
    assert result.twist.hook == "Dust and debris obscure the battlefield."


def test_generate_mission_no_twist_when_not_requested(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)
    twists = Path(summaries_dir) / "missions" / "twists"
    twists.mkdir(parents=True)
    _write_twist(twists, "ash-fall", "Ash Fall")

    result = generate_mission(summaries_dir, include_twist=False)

    assert result.twist is None


def test_generate_mission_uses_specified_deployment(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)
    extra = Path(summaries_dir) / "missions" / "deployment-zones"
    _write_deployment(extra, "hammer-and-anvil", "Hammer and Anvil", ["heresy3"])

    result = generate_mission(summaries_dir, deployment_slug="hammer-and-anvil")

    assert result.deployment.slug == "hammer-and-anvil"
    assert result.deployment.title == "Hammer and Anvil"


def test_generate_mission_accepts_spaced_deployment_name(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)

    result = generate_mission(summaries_dir, deployment_slug="dawn of war")

    assert result.deployment.slug == "dawn-of-war"


def test_generate_mission_raises_on_unknown_deployment(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)

    with pytest.raises(MissionGeneratorError, match="not found"):
        generate_mission(summaries_dir, deployment_slug="made-up-zone")


def test_generate_mission_excludes_titan_primaries_by_default(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)
    primaries = Path(summaries_dir) / "missions" / "primary-objectives"
    _write_primary(primaries, "engine-kill", "Engine Kill", titan=True)

    results = [generate_mission(summaries_dir) for _ in range(20)]

    assert all(r.primary.slug != "engine-kill" for r in results)


def test_generate_mission_includes_titan_primaries_when_enabled(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)
    primaries = Path(summaries_dir) / "missions" / "primary-objectives"
    _write_primary(primaries, "engine-kill", "Engine Kill", titan=True)

    results = [generate_mission(summaries_dir, titans=True) for _ in range(30)]
    slugs = {r.primary.slug for r in results}

    assert "engine-kill" in slugs


def test_generate_mission_secondary_points_within_default_range(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)

    for _ in range(30):
        result = generate_mission(summaries_dir)
        for s in result.secondaries:
            assert 1 <= s.points <= 4


def test_generate_mission_secondary_points_min_is_one(tmp_path: Path) -> None:
    """Secondary objectives can score as low as 1VP."""
    summaries_dir = _build_missions_dir(tmp_path)

    points_seen: set[int] = set()
    for _ in range(200):
        result = generate_mission(summaries_dir)
        for s in result.secondaries:
            points_seen.add(s.points)

    assert 1 in points_seen


def test_generate_mission_max_secondary_points_caps_range(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)

    for _ in range(30):
        result = generate_mission(summaries_dir, max_secondary_points=2)
        for s in result.secondaries:
            assert 1 <= s.points <= 2


def test_generate_mission_max_secondary_points_below_min_raises(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)

    with pytest.raises(MissionGeneratorError, match="max_secondary_points must be at least"):
        generate_mission(summaries_dir, max_secondary_points=0)


def test_generate_mission_raises_when_missions_dir_missing(tmp_path: Path) -> None:
    with pytest.raises(MissionGeneratorError, match="does not exist"):
        generate_mission(str(tmp_path / "nowhere"))


def test_format_mission_fits_discord_limit(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)
    twists = Path(summaries_dir) / "missions" / "twists"
    twists.mkdir(parents=True)
    _write_twist(twists, "ash-fall", "Ash Fall")

    result = generate_mission(summaries_dir, include_twist=True)

    assert len(format_mission(result)) <= 2000


def test_format_mission_primary_title_is_header(tmp_path: Path) -> None:
    """The primary mission name is the bold title at the top of the post."""
    summaries_dir = _build_missions_dir(tmp_path)

    result = generate_mission(summaries_dir)
    message = format_mission(result)
    first_line = message.splitlines()[0]

    assert first_line == "**The Heart of Battle**"


def test_format_mission_escapes_titles() -> None:
    # Body content comes from admin-contributed markdown files and is trusted.
    # Only titles (extracted from file H1s) are passed through escape_public_text.
    entry = MissionEntry(
        slug="test",
        title="@everyone **Attack**",
        source_editions=("heresy3",),
        titan_mission=False,
        points_minimum=None,
        body="Standard objective rules apply.",
        hook="",
    )
    result = MissionResult(
        primary=entry,
        deployment=MissionEntry(
            slug="test-zone",
            title="@here Zone",
            source_editions=("heresy3",),
            titan_mission=False,
            points_minimum=None,
            body="Deploy within 12\" of your edge.",
            hook="",
        ),
        secondaries=(),
        twist=None,
    )

    message = format_mission(result)

    # Titles are escaped — @mentions and markdown formatting should be neutralised.
    assert "@everyone" not in message
    assert "\\*\\*Attack\\*\\*" in message


def test_format_mission_shows_edition_tag(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)

    result = generate_mission(summaries_dir)
    message = format_mission(result)

    assert "HH3" in message or "HH2" in message


def test_format_mission_includes_twist_hook(tmp_path: Path) -> None:
    summaries_dir = _build_missions_dir(tmp_path)
    twists = Path(summaries_dir) / "missions" / "twists"
    twists.mkdir(parents=True)
    _write_twist(twists, "ash-fall", "Ash Fall")

    result = generate_mission(summaries_dir, include_twist=True)
    message = format_mission(result)

    assert "Dust and debris obscure the battlefield." in message


def test_format_mission_deployment_includes_measurements(tmp_path: Path) -> None:
    """Deployment zone output includes the standard 6'×4' measurement line."""
    summaries_dir = _build_missions_dir(tmp_path)

    result = generate_mission(summaries_dir)
    message = format_mission(result)

    assert "Standard 6'×4' table:" in message
    assert "12\"×48\" per side" in message


# ── Inline YAML comment parsing ──────────────────────────────────────────────

def test_parse_entry_strips_inline_yaml_comments(tmp_path: Path) -> None:
    """Frontmatter values with trailing # comments parse as if the comment were absent."""
    directory = tmp_path / "primary-objectives"
    directory.mkdir()
    (directory / "commented.md").write_text(
        "---\n"
        "name: commented\n"
        "source_editions: []  # TODO: tag this later\n"
        "titan_mission: false  # not a titan mission\n"
        "cooperative: false  # standard 1v1\n"
        "points_minimum: null\n"
        "faction_tags: []\n"
        "---\n\n"
        "# Commented Mission\n\nSome rules.\n",
        encoding="utf-8",
    )
    entries = _load_entries(directory)
    assert len(entries) == 1
    assert entries[0].source_editions == ()
    assert entries[0].titan_mission is False
    assert entries[0].cooperative is False


def test_parse_entry_inline_comment_on_non_empty_list(tmp_path: Path) -> None:
    """A comment after a populated list value still parses the list correctly."""
    directory = tmp_path / "primary-objectives"
    directory.mkdir()
    (directory / "tagged.md").write_text(
        "---\n"
        "name: tagged\n"
        "source_editions: [heresy3]  # confirmed HH3\n"
        "titan_mission: false\n"
        "points_minimum: null\n"
        "faction_tags: []\n"
        "---\n\n"
        "# Tagged Mission\n\nSome rules.\n",
        encoding="utf-8",
    )
    entries = _load_entries(directory)
    assert entries[0].source_editions == ("heresy3",)


# ── Cooperative mission exclusion ─────────────────────────────────────────────

def _write_cooperative_primary(directory: Path, slug: str, title: str) -> None:
    (directory / f"{slug}.md").write_text(
        f"---\nname: {slug}\nsource_editions: []\ncooperative: true\ntitan_mission: false\npoints_minimum: null\nfaction_tags: []\n---\n\n# {title}\n\nA 2v2 cooperative mission.\n",
        encoding="utf-8",
    )


def test_generate_mission_excludes_cooperative_primaries(tmp_path: Path) -> None:
    """Cooperative missions are never selected in random generation."""
    summaries_dir = _build_missions_dir(tmp_path)
    primaries = Path(summaries_dir) / "missions" / "primary-objectives"
    _write_cooperative_primary(primaries, "hydram-necare", "Hydram Necare")

    results = [generate_mission(summaries_dir) for _ in range(30)]
    assert all(r.primary.slug != "hydram-necare" for r in results)


def test_generate_mission_raises_when_all_primaries_are_cooperative(tmp_path: Path) -> None:
    """If only cooperative primaries exist, generate_mission raises rather than returning one."""
    summaries_dir = _build_missions_dir(tmp_path)
    primaries = Path(summaries_dir) / "missions" / "primary-objectives"
    # Replace the existing non-cooperative primary with a cooperative one
    (primaries / "the-heart-of-battle.md").write_text(
        "---\nname: the-heart-of-battle\nsource_editions: [heresy3]\ncooperative: true\ntitan_mission: false\npoints_minimum: null\nfaction_tags: []\n---\n\n# The Heart of Battle\n\nRules.\n",
        encoding="utf-8",
    )
    with pytest.raises(MissionGeneratorError, match="No primary objectives"):
        generate_mission(summaries_dir)


# ── BFWG edition tag ──────────────────────────────────────────────────────────

def test_format_mission_shows_bfwg_edition_tag(tmp_path: Path) -> None:
    """BFWG custom missions render a readable 'BFWG' label in the output."""
    summaries_dir = str(tmp_path / "summaries")
    missions = tmp_path / "summaries" / "missions"

    deployments = missions / "deployment-zones"
    deployments.mkdir(parents=True)
    _write_deployment(deployments, "search-and-destroy", "Search and Destroy", ["bfwg"])

    primaries = missions / "primary-objectives"
    primaries.mkdir(parents=True)
    _write_primary(primaries, "supply-drop", "Supply Drop")

    secondaries = missions / "secondary-objectives"
    secondaries.mkdir(parents=True)
    _write_secondary(secondaries, "slay-the-warlord", "Slay the Warlord", 3000)

    result = generate_mission(summaries_dir, deployment_slug="search-and-destroy")
    message = format_mission(result)

    assert "BFWG" in message


def test_format_mission_no_edition_tag_when_source_editions_empty(tmp_path: Path) -> None:
    """Missions with no source_editions show no edition parenthetical."""
    entry = MissionEntry(
        slug="test",
        title="Test Mission",
        source_editions=(),
        titan_mission=False,
        points_minimum=None,
        body="Some rules.",
        hook="",
    )
    result = MissionResult(
        primary=entry,
        deployment=MissionEntry(
            slug="test-zone",
            title="Test Zone",
            source_editions=(),
            titan_mission=False,
            points_minimum=None,
            body='Deploy within 12" of your edge.',
            hook="",
        ),
        secondaries=(),
        twist=None,
    )
    message = format_mission(result)
    assert " *(" not in message


# ── Parsing edge cases ────────────────────────────────────────────────────────

def test_parse_list_multi_item_source_editions(tmp_path: Path) -> None:
    """Multi-item source_editions lists are parsed correctly."""
    directory = tmp_path / "deployment-zones"
    directory.mkdir()
    (directory / "multi.md").write_text(
        "---\nname: multi\nsource_editions: [heresy3, heresy2]\ntitan_mission: false\npoints_minimum: null\nfaction_tags: []\n---\n\n# Multi Edition Zone\n\nDeploy within 12\".\n",
        encoding="utf-8",
    )
    entries = _load_entries(directory)
    assert entries[0].source_editions == ("heresy3", "heresy2")


def test_generate_mission_raises_when_primary_dir_empty(tmp_path: Path) -> None:
    """An empty primary-objectives directory raises rather than silently returning no mission."""
    missions = tmp_path / "missions"

    primaries = missions / "primary-objectives"
    primaries.mkdir(parents=True)

    deployments = missions / "deployment-zones"
    deployments.mkdir()
    _write_deployment(deployments, "dawn-of-war", "Dawn of War", ["heresy3"])

    secondaries = missions / "secondary-objectives"
    secondaries.mkdir()
    _write_secondary(secondaries, "slay-the-warlord", "Slay the Warlord")

    with pytest.raises(MissionGeneratorError, match="No primary objectives"):
        generate_mission(str(tmp_path))


# ── Scoring text extraction ───────────────────────────────────────────────────

def _make_entry(body: str) -> MissionEntry:
    return MissionEntry(
        slug="test",
        title="Test Mission",
        source_editions=("heresy3",),
        titan_mission=False,
        points_minimum=None,
        body=body,
        hook="",
    )


def test_objective_placement_prefers_explicit_section() -> None:
    """A dedicated Objective Placement section is returned as-is."""
    body = (
        "## Objective Placement\n\n"
        "Place four objectives in the centre of each board quarter.\n\n"
        "## Victory Conditions\n\n"
        "Each objective is worth **2VP**.\n"
    )
    result = _objective_placement_text(_make_entry(body))
    assert result == "Place four objectives in the centre of each board quarter."


def test_objective_placement_extracts_setup_objective_sentence() -> None:
    """Objective placement can be extracted from setup text without deployment order."""
    body = (
        "## Set Up\n\n"
        "Roll off to see who deploys first. "
        "Players then place five objectives: one in the centre and one in each table quarter.\n\n"
        "The player who deploys first goes first.\n\n"
        "## Victory Conditions\n\n"
        "Each objective is worth **1VP**.\n"
    )
    result = _objective_placement_text(_make_entry(body))
    assert "place five objectives" in result
    assert "deploys first" not in result


def test_format_mission_includes_objective_placement_section(tmp_path: Path) -> None:
    """Formatted mission output shows objective placement before deployment zone details."""
    summaries_dir = _build_missions_dir(tmp_path)
    primaries = Path(summaries_dir) / "missions" / "primary-objectives"
    (primaries / "the-heart-of-battle.md").write_text(
        "---\nname: the-heart-of-battle\nsource_editions: [heresy3]\n"
        "titan_mission: false\npoints_minimum: null\nfaction_tags: []\n---\n\n"
        "# The Heart of Battle\n\n"
        "## Objective Placement\n\n"
        "Place one objective in the centre of the battlefield.\n\n"
        "## Victory Conditions\n\n"
        "Control the central objective for **3VP**.\n",
        encoding="utf-8",
    )

    result = generate_mission(summaries_dir)
    message = format_mission(result)

    assert "**Objective Placement**" in message
    assert "Place one objective in the centre" in message
    assert message.index("**Objective Placement**") < message.index("**Deployment Zone:**")


def test_scoring_text_extracts_victory_conditions_section() -> None:
    """Victory Conditions section content is returned when present."""
    body = (
        "*Flavour text here.*\n\n"
        "## Set Up\n\nDeploy within 12\" of your edge.\n\n"
        "## Victory Conditions\n\n"
        "**Primary Mission:** Control objectives. Each is worth **2VP**.\n\n"
        "**Secondary Mission Objectives**\n- Slay the Warlord (2VP)\n\n"
        "## Special Rules\n\nReserves apply.\n"
    )
    result = _scoring_text(_make_entry(body))
    assert "2VP" in result
    assert "Control objectives" in result
    assert "Flavour text" not in result
    assert "Deploy within 12" not in result


def test_scoring_text_extracts_qualified_victory_conditions_heading() -> None:
    """Victory Conditions headings can include a mission-objective qualifier."""
    body = (
        "*Flavour text here.*\n\n"
        "## Victory Conditions: Vital Ground\n\n"
        "**Primary Objectives**\n"
        "- Capture Objective Markers\n\n"
        "Each objective is worth **2VP**.\n\n"
        "## Mission Special Rules\n\nReserves apply.\n"
    )
    result = _scoring_text(_make_entry(body))
    assert "2VP" in result
    assert "Flavour text" not in result


def test_scoring_text_falls_back_to_first_paragraph_when_no_section() -> None:
    """Missions without a Victory Conditions section use the first paragraph."""
    body = "One central objective worth 3VP. The player controlling most objectives wins.\n\nStandard control rules apply."
    result = _scoring_text(_make_entry(body))
    assert "3VP" in result


def test_scoring_text_skips_html_comments_in_fallback() -> None:
    """HTML comment blocks are never returned as the scoring text."""
    body = (
        "<!-- SOURCE: some-file.pdf -->\n\n"
        "One central objective worth 3VP.\n"
    )
    result = _scoring_text(_make_entry(body))
    assert "<!--" not in result
    assert "3VP" in result


def test_scoring_text_strips_secondary_section_from_victory_conditions() -> None:
    """Secondary Objectives block is removed from Victory Conditions so it is not listed twice."""
    body = (
        "## Victory Conditions\n\n"
        "**Primary Objectives**\n\nControl the reactor — 1VP per turn.\n\n"
        "**Secondary Objectives**\n"
        "- Slay the Warlord (2VP)\n"
        "- Last Man Standing (2VP)\n\n"
        "## Special Rules\n\nReserves apply.\n"
    )
    result = _scoring_text(_make_entry(body))
    assert "reactor" in result
    assert "Slay the Warlord" not in result
    assert "Last Man Standing" not in result


def test_scoring_text_strips_secondary_missions_heading_variant() -> None:
    """Secondary Missions blocks are also stripped from Victory Conditions."""
    body = (
        "## Victory Conditions\n\n"
        "**Primary Mission**\n\nCapture objective markers for **2VP**.\n\n"
        "**Secondary Missions**\n"
        "- Slay the Warlord (2VP)\n"
        "- Giant Killer (2VP)\n\n"
        "## Objectives\n\nEach objective scatters D6\".\n"
    )
    result = _scoring_text(_make_entry(body))
    assert "2VP" in result
    assert "Slay the Warlord" not in result
    assert "Giant Killer" not in result
    assert "Secondary Missions" not in result


def test_scoring_text_excludes_heading_level_secondary_section() -> None:
    """Heading-level secondary sections are outside Victory Conditions."""
    body = (
        "## Victory Conditions\n\n"
        "**Primary Mission**\n\nCapture objective markers for **2VP**.\n\n"
        "## Secondary Objectives\n\n"
        "- Slay the Warlord (2VP)\n"
        "- Giant Killer (2VP)\n\n"
        "## Objectives\n\nEach objective scatters D6\".\n"
    )
    result = _scoring_text(_make_entry(body))
    assert "2VP" in result
    assert "Slay the Warlord" not in result
    assert "Giant Killer" not in result
    assert "Secondary Objectives" not in result


def test_scoring_text_preserves_primary_rules_after_secondary_list() -> None:
    """Only the secondary bullet list is stripped, not later primary scoring text."""
    body = (
        "## Victory Conditions: Gain Ground\n\n"
        "**Primary Objectives**\n"
        "- Capture Objective Markers\n\n"
        "**Secondary Objectives**\n"
        "- Slay the Warlord (3VP)\n"
        "- Giant Killer (2VP)\n\n"
        "The central objective is worth **3VP** and each flank objective is worth **2VP**.\n"
    )
    result = _scoring_text(_make_entry(body))
    assert "central objective" in result
    assert "3VP" in result
    assert "Slay the Warlord" not in result


def test_format_mission_shows_victory_conditions_not_flavour_text(tmp_path: Path) -> None:
    """format_mission output includes scoring info, not just flavour/setup text."""
    summaries_dir = _build_missions_dir(tmp_path)
    # Replace the default primary with one that has a Victory Conditions section
    primaries = Path(summaries_dir) / "missions" / "primary-objectives"
    (primaries / "the-heart-of-battle.md").write_text(
        "---\nname: the-heart-of-battle\nsource_editions: [heresy3]\n"
        "titan_mission: false\npoints_minimum: null\nfaction_tags: []\n---\n\n"
        "# The Heart of Battle\n\n"
        "*Push through the enemy line and seize the centre.*\n\n"
        "## Set Up\n\nDeploy within 12\" of your long table edge.\n\n"
        "## Victory Conditions\n\n"
        "Control the central objective for **3VP**. Peripheral objectives worth **1VP** each.\n",
        encoding="utf-8",
    )

    result = generate_mission(summaries_dir)
    message = format_mission(result)

    assert "3VP" in message
    assert "Push through the enemy line" not in message


def test_format_mission_secondaries_not_duplicated_when_in_victory_conditions(tmp_path: Path) -> None:
    """Secondaries listed in the primary's Victory Conditions body are stripped, not shown twice."""
    summaries_dir = _build_missions_dir(tmp_path)
    primaries = Path(summaries_dir) / "missions" / "primary-objectives"
    (primaries / "the-heart-of-battle.md").write_text(
        "---\nname: the-heart-of-battle\nsource_editions: [heresy3]\n"
        "titan_mission: false\npoints_minimum: null\nfaction_tags: []\n---\n\n"
        "# The Heart of Battle\n\n"
        "## Victory Conditions\n\n"
        "**Primary Objectives**\n\nControl objectives for **3VP**.\n\n"
        "**Secondary Objectives**\n"
        "- Slay the Warlord (2VP)\n"
        "- Last Man Standing (2VP)\n",
        encoding="utf-8",
    )

    result = generate_mission(summaries_dir)
    message = format_mission(result)

    # Each secondary should appear at most once (the bot's generated list)
    assert message.count("Slay the Warlord") == 1
    assert message.count("Last Man Standing") == 1


# ── Real mission files integration tests ─────────────────────────────────────

_real_summaries_present = pytest.mark.skipif(
    not _REAL_SUMMARIES.exists(),
    reason="summaries directory not present relative to discord-bot",
)


@_real_summaries_present
def test_real_missions_all_parse_without_error() -> None:
    """generate_mission succeeds on the real summaries directory."""
    result = generate_mission(str(_REAL_SUMMARIES))
    assert result.primary is not None
    assert result.deployment is not None
    assert len(result.secondaries) > 0


@_real_summaries_present
def test_real_primaries_all_have_nontrivial_scoring_text() -> None:
    """Every non-cooperative primary mission produces scoring text that mentions VP."""
    entries = _load_entries(_REAL_SUMMARIES / "missions" / "primary-objectives")
    assert entries, "Expected at least one primary-objective file"
    for entry in entries:
        if entry.cooperative:
            continue
        score = _scoring_text(entry)
        has_vp = "VP" in score or "Victory Point" in score or "score" in score.lower()
        assert has_vp, (
            f"{entry.slug!r} scoring text has no VP/scoring mention: {score!r}"
        )


@_real_summaries_present
def test_real_primaries_no_generic_titles() -> None:
    """No primary mission in the pool uses a generic placeholder title."""
    entries = _load_entries(_REAL_SUMMARIES / "missions" / "primary-objectives")
    generic_titles = {"Mission 1", "Mission 2", "Mission 3", "Mission 4"}
    for entry in entries:
        assert entry.title not in generic_titles, (
            f"{entry.slug!r} has generic placeholder title {entry.title!r}"
        )


@_real_summaries_present
def test_real_twists_all_have_narrative_hooks() -> None:
    """Every twist file has a non-empty Narrative Hook."""
    entries = _load_entries(_REAL_SUMMARIES / "missions" / "twists")
    assert entries, "Expected at least one twist file"
    for entry in entries:
        assert entry.hook, f"Twist {entry.slug!r} has no narrative hook"


@_real_summaries_present
def test_real_missions_format_fits_discord_limit() -> None:
    """format_mission output is within Discord's 2000-character limit for every primary."""
    missions_root = _REAL_SUMMARIES / "missions"
    primaries = [
        e for e in _load_entries(missions_root / "primary-objectives")
        if not e.cooperative
    ]
    deployments = _load_entries(missions_root / "deployment-zones")
    secondaries = _load_entries(missions_root / "secondary-objectives")
    assert primaries and deployments and secondaries

    fixed_secondaries = tuple(
        SecondaryResult(title=s.title, slug=s.slug, points=2) for s in secondaries
    )
    for primary in primaries:
        result = MissionResult(
            primary=primary,
            deployment=deployments[0],
            secondaries=fixed_secondaries,
            twist=None,
        )
        msg = format_mission(result)
        assert len(msg) <= 2000, (
            f"{primary.slug!r} exceeds Discord 2000-char limit: {len(msg)} chars"
        )


@_real_summaries_present
def test_real_primaries_no_stale_status_comments() -> None:
    """No primary mission file contains a STATUS: needs-review HTML comment."""
    primaries_dir = _REAL_SUMMARIES / "missions" / "primary-objectives"
    for path in sorted(primaries_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert "STATUS: needs-review" not in text, (
            f"{path.name} still contains a 'STATUS: needs-review' comment"
        )


@_real_summaries_present
def test_real_primaries_no_frontmatter_todo_comments() -> None:
    """No primary mission file has inline TODO comments in its frontmatter."""
    primaries_dir = _REAL_SUMMARIES / "missions" / "primary-objectives"
    for path in sorted(primaries_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 4)
        if end == -1:
            continue
        frontmatter = text[4:end]
        assert "# TODO" not in frontmatter, (
            f"{path.name} has a '# TODO' comment in its frontmatter"
        )


def test_get_primary_mission_titles_sorted_and_cached(tmp_path: Path) -> None:
    mission_generator.reset()
    summaries_dir = str(_build_missions_dir(tmp_path))

    titles = mission_generator.get_primary_mission_titles(summaries_dir)

    assert titles == sorted(titles)
    assert len(titles) >= 1

    primaries = Path(summaries_dir) / "missions" / "primary-objectives"
    _write_primary(primaries, "later-added", "Zeta Assault")

    assert mission_generator.get_primary_mission_titles(summaries_dir) == titles

    mission_generator.reset()
    assert "Zeta Assault" in mission_generator.get_primary_mission_titles(summaries_dir)


def test_autocomplete_mission_title_matches_substring(tmp_path: Path) -> None:
    mission_generator.reset()
    summaries_dir = str(_build_missions_dir(tmp_path))
    primaries = Path(summaries_dir) / "missions" / "primary-objectives"
    _write_primary(primaries, "onslaught", "Onslaught")
    _write_primary(primaries, "blood-feud", "Blood Feud")

    assert mission_generator.autocomplete_mission_title(summaries_dir, "slau") == [
        "Onslaught"
    ]
    assert mission_generator.autocomplete_mission_title(summaries_dir, "zzz") == []
    everything = mission_generator.autocomplete_mission_title(summaries_dir, "")
    assert "Blood Feud" in everything


def test_autocomplete_mission_title_caps_at_25(tmp_path: Path) -> None:
    mission_generator.reset()
    summaries_dir = str(_build_missions_dir(tmp_path))
    primaries = Path(summaries_dir) / "missions" / "primary-objectives"
    for n in range(30):
        _write_primary(primaries, f"mission-{n:02d}", f"Common Mission {n:02d}")

    assert len(mission_generator.autocomplete_mission_title(summaries_dir, "common")) == 25
