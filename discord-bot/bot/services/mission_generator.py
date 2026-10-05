import random
import re
from dataclasses import dataclass
from pathlib import Path

from bot.services.discord_text import escape_public_text
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT


SECONDARY_POINTS_MIN = 1
SECONDARY_POINTS_MAX = 4

_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_LIST_RE = re.compile(r"^\[([^\]]*)\]$")
_HOOK_SECTION_RE = re.compile(
    r"^##\s+Narrative Hook\s*\n(.*?)(?=^##|\Z)",
    re.MULTILINE | re.DOTALL,
)
_SECTION_RE = re.compile(
    r"^##\s+([^\n]+)\n(.*?)(?=^##|\Z)",
    re.MULTILINE | re.DOTALL,
)
_VICTORY_RE = re.compile(
    r"^##\s+Victory Conditions[^\n]*\n(.*?)(?=^##|\Z)",
    re.MULTILINE | re.DOTALL,
)
# Strip the Secondary Objectives sub-block from Victory Conditions text so
# the bot's own generated secondary list is the single authoritative listing.
_SECONDARY_SECTION_RE = re.compile(
    r"(?:^|\n+)\*\*Secondary(?:\s+Mission(?:s|\s+Objectives?)?|\s+Objectives?)?:?\*\*"
    r"[^\n]*(?:\n[ \t]*-[^\n]*)*\n?",
    re.MULTILINE,
)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_PLACEMENT_TERM_RE = re.compile(
    r"\b(objectives?|markers?|pylon|rift|governor|operatives?|relic|sectors?)\b",
    re.IGNORECASE,
)
_PLACEMENT_CONTEXT_RE = re.compile(
    r"\b("
    r"place|placed|placing|deploy|deployed|deployment|set up|centre|center|central|"
    r"quarter|quadrant|zone|zones|map|diagram|table|scatter|scattered|half|"
    r"middle|mid-field|opposing|relative|starting|randomly|restrictions"
    r")\b",
    re.IGNORECASE,
)
_SETUP_CONTEXT_RE = re.compile(
    r"\b(table|diagram|map|section|sections|zone|zones|battlefield|no man's land)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class MissionGeneratorError(RuntimeError):
    """Raised when a mission cannot be generated."""


@dataclass(frozen=True)
class MissionEntry:
    slug: str
    title: str
    source_editions: tuple[str, ...]
    titan_mission: bool
    points_minimum: int | None
    body: str
    hook: str
    cooperative: bool = False


@dataclass(frozen=True)
class SecondaryResult:
    title: str
    slug: str
    points: int


@dataclass(frozen=True)
class MissionResult:
    primary: MissionEntry
    deployment: MissionEntry
    secondaries: tuple[SecondaryResult, ...]
    twist: MissionEntry | None


def generate_mission(
    summaries_dir: str,
    deployment_slug: str | None = None,
    titans: bool = False,
    include_twist: bool = False,
    max_secondary_points: int | None = None,
    rng: random.Random | None = None,
) -> MissionResult:
    missions_root = Path(summaries_dir) / "missions"

    if not missions_root.exists():
        raise MissionGeneratorError(f"Missions directory does not exist: {missions_root}")

    if max_secondary_points is not None and max_secondary_points < SECONDARY_POINTS_MIN:
        raise MissionGeneratorError(
            f"max_secondary_points must be at least {SECONDARY_POINTS_MIN}."
        )

    secondary_max = max_secondary_points if max_secondary_points is not None else SECONDARY_POINTS_MAX

    mission_rng = rng or random.SystemRandom()

    primaries = _load_entries(missions_root / "primary-objectives")
    primaries = [e for e in primaries if not e.cooperative]
    if not titans:
        primaries = [e for e in primaries if not e.titan_mission]
    if not primaries:
        raise MissionGeneratorError("No primary objectives found in missions/primary-objectives/.")

    deployments = _load_entries(missions_root / "deployment-zones")
    if not deployments:
        raise MissionGeneratorError("No deployment zones found in missions/deployment-zones/.")

    secondaries = _load_entries(missions_root / "secondary-objectives")
    if not secondaries:
        raise MissionGeneratorError("No secondary objectives found in missions/secondary-objectives/.")

    primary = mission_rng.choice(primaries)

    if deployment_slug is not None:
        target = _normalise_slug(deployment_slug)
        matched = [d for d in deployments if d.slug == target]
        if not matched:
            available = ", ".join(sorted(d.slug for d in deployments))
            raise MissionGeneratorError(
                f"Deployment zone '{deployment_slug}' not found. Available: {available}"
            )
        deployment = matched[0]
    else:
        deployment = mission_rng.choice(deployments)

    secondary_results = tuple(
        SecondaryResult(
            title=s.title,
            slug=s.slug,
            points=mission_rng.randint(SECONDARY_POINTS_MIN, secondary_max),
        )
        for s in secondaries
    )

    twist: MissionEntry | None = None
    if include_twist:
        twist_entries = _load_entries(missions_root / "twists")
        if twist_entries:
            twist = mission_rng.choice(twist_entries)

    return MissionResult(
        primary=primary,
        deployment=deployment,
        secondaries=secondary_results,
        twist=twist,
    )


def format_mission(result: MissionResult) -> str:
    edition_tag = _edition_tag(result.deployment.source_editions)
    primary_body = _scoring_text(result.primary)
    objective_placement = _objective_placement_text(result.primary)
    deployment_body = result.deployment.body.strip()

    lines: list[str] = [
        f"**{escape_public_text(result.primary.title)}**",
        "",
        primary_body,
    ]

    if objective_placement:
        lines += [
            "",
            "**Objective Placement**",
            objective_placement,
        ]

    lines += [
        "",
        f"**Deployment Zone:** {escape_public_text(result.deployment.title)}{edition_tag}",
        deployment_body,
        "",
        "**Secondary Objectives**",
    ]

    for s in result.secondaries:
        lines.append(f"- {escape_public_text(s.title)} — {s.points}VP")

    if result.twist is not None:
        twist_body = _first_paragraph(result.twist.body)
        lines += [
            "",
            f"**Narrative Twist:** {escape_public_text(result.twist.title)}",
            twist_body,
        ]
        if result.twist.hook:
            lines.append(f"*{result.twist.hook.strip()}*")

    message = "\n".join(lines)

    if len(message) > DISCORD_MESSAGE_LIMIT:
        return message[: DISCORD_MESSAGE_LIMIT - 3].rstrip() + "..."

    return message


_title_cache: dict[str, list[str]] = {}


def get_primary_mission_titles(summaries_dir: str) -> list[str]:
    """Sorted, deduplicated primary-mission titles, cached per summaries dir."""
    if summaries_dir in _title_cache:
        return _title_cache[summaries_dir]

    entries = _load_entries(Path(summaries_dir) / "missions" / "primary-objectives")
    titles = sorted({entry.title for entry in entries})
    _title_cache[summaries_dir] = titles
    return titles


def autocomplete_mission_title(summaries_dir: str, query: str) -> list[str]:
    """Return up to 25 mission titles matching query as a case-insensitive substring."""
    titles = get_primary_mission_titles(summaries_dir)
    q = query.strip().lower()

    if not q:
        return titles[:25]

    return [title for title in titles if q in title.lower()][:25]


def reset() -> None:
    _title_cache.clear()


def _load_entries(directory: Path) -> list[MissionEntry]:
    if not directory.exists():
        return []

    entries: list[MissionEntry] = []

    for path in sorted(directory.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        entry = _parse_entry(path.stem, text)

        if entry is not None:
            entries.append(entry)

    return entries


def _parse_entry(slug: str, text: str) -> MissionEntry | None:
    frontmatter, body = _split_frontmatter(text)

    title_match = _H1_RE.search(body)
    title = (
        title_match.group(1).strip()
        if title_match
        else slug.replace("-", " ").title()
    )

    source_editions = tuple(_parse_list(frontmatter.get("source_editions", "[]")))
    titan_mission = frontmatter.get("titan_mission", "false").strip().lower() == "true"
    cooperative = frontmatter.get("cooperative", "false").strip().lower() == "true"

    points_min_raw = frontmatter.get("points_minimum", "null").strip()
    points_minimum = None if points_min_raw == "null" else _safe_int(points_min_raw)

    hook_match = _HOOK_SECTION_RE.search(body)
    hook = hook_match.group(1).strip() if hook_match else ""

    clean_body = _H1_RE.sub("", body, count=1).strip()

    return MissionEntry(
        slug=slug,
        title=title,
        source_editions=source_editions,
        titan_mission=titan_mission,
        points_minimum=points_minimum,
        body=clean_body,
        hook=hook,
        cooperative=cooperative,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text

    end = text.find("\n---", 4)

    if end == -1:
        return {}, text

    frontmatter_text = text[4:end].strip()
    body = text[end + 4:].lstrip("\n")

    result: dict[str, str] = {}

    for line in frontmatter_text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            # Strip inline YAML comments (e.g. `[]  # TODO: ...`)
            value = re.sub(r"\s+#.*$", "", value).strip()
            result[key.strip()] = value

    return result, body


def _parse_list(value: str) -> list[str]:
    stripped = value.strip()
    m = _LIST_RE.match(stripped)

    if not m:
        return [stripped] if stripped else []

    inner = m.group(1).strip()

    if not inner:
        return []

    return [item.strip() for item in inner.split(",") if item.strip()]


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _normalise_slug(value: str) -> str:
    return value.strip().lower().replace(" ", "-").replace("_", "-")


def _edition_tag(editions: tuple[str, ...]) -> str:
    if not editions:
        return ""

    labels: dict[str, str] = {
        "heresy3": "HH3",
        "heresy2": "HH2",
        "40k10": "40K 10th",
        "40k9": "40K 9th",
        "bfwg": "BFWG",
    }

    rendered = [labels.get(e, e) for e in editions]
    return f" *({' / '.join(rendered)})*"


def _scoring_text(entry: "MissionEntry") -> str:
    """Return the Victory Conditions section (minus secondary objectives), or the first clean paragraph."""
    victory_match = _VICTORY_RE.search(entry.body)
    if victory_match:
        text = _SECONDARY_SECTION_RE.sub("", victory_match.group(1)).strip()
        if text:
            return text
    return _first_paragraph(entry.body)


def _objective_placement_text(entry: MissionEntry) -> str:
    """Return a concise objective placement note for the mission, when one can be found."""
    explicit = _section_text(entry.body, "objective placement")
    if explicit:
        return explicit

    candidate_sections = (
        ("set up", True),
        ("deployment", True),
        ("advancing objectives", False),
        ("deployment map", False),
        ("victory conditions", False),
        ("objectives", False),
    )

    for section_name, include_context in candidate_sections:
        section = _section_text(entry.body, section_name)
        if not section:
            continue

        if section_name == "victory conditions":
            section = _SECONDARY_SECTION_RE.sub("", section)

        placement = _placement_from_text(section, include_context=include_context)
        if placement:
            return placement

    return ""


def _section_text(text: str, heading: str) -> str:
    target = heading.strip().lower()

    for match in _SECTION_RE.finditer(text):
        candidate = match.group(1).strip().lower()
        if candidate == target or candidate.startswith(f"{target}:"):
            return match.group(2).strip()

    return ""


def _placement_from_text(text: str, include_context: bool = False) -> str:
    clean = _CODE_BLOCK_RE.sub("", text)
    blocks = [
        block.strip()
        for block in re.split(r"\n{2,}", clean)
        if block.strip() and not block.strip().startswith("<!--")
    ]

    selected: list[str] = []

    for index, block in enumerate(blocks):
        if _SECONDARY_SECTION_RE.match(block):
            break
        if not _looks_like_objective_placement(block):
            continue

        if (
            include_context
            and not selected
            and index > 0
            and _looks_like_setup_context(blocks[index - 1])
        ):
            selected.append(blocks[index - 1])

        selected_block = _placement_sentences(block)
        if selected_block:
            selected.append(selected_block)

    return "\n\n".join(selected).strip()


def _looks_like_objective_placement(text: str) -> bool:
    return bool(_PLACEMENT_TERM_RE.search(text) and _PLACEMENT_CONTEXT_RE.search(text))


def _looks_like_setup_context(text: str) -> bool:
    return bool(_SETUP_CONTEXT_RE.search(text))


def _placement_sentences(text: str) -> str:
    sentences = [
        sentence.strip()
        for sentence in _SENTENCE_SPLIT_RE.split(text)
        if sentence.strip()
    ]
    selected = [
        sentence
        for sentence in sentences
        if _looks_like_objective_placement(sentence)
    ]

    if not selected and _looks_like_objective_placement(text):
        return text.strip()

    return " ".join(selected)


def _first_paragraph(text: str) -> str:
    for block in text.split("\n\n"):
        stripped = block.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("<!--"):
            return stripped
    return text.strip()
