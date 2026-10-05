import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

from bot.services.discord_text import escape_public_text
from bot.services.text_utils import (
    DISCORD_MESSAGE_LIMIT,
    best_excerpt as _best_excerpt,
    tokens as _tokens,
)


MAX_EXCERPT_LENGTH = 220

MISSION_TYPES = (
    "decapitation strike",
    "armoured breakthrough",
    "zone mortalis raid",
    "void-drop assault",
    "relief operation",
    "data-vault seizure",
    "rearguard action",
    "hunter-killer sweep",
)

THEATRES = (
    "a shattered orbital dock",
    "a dust-choked manufactorum belt",
    "a fortress-city outer curtain",
    "a broken compliance zone",
    "a rad-scarred refinery moon",
    "a shrine district under evacuation",
    "a blackshield supply corridor",
    "a storm-lashed landing field",
)

OBJECTIVES = (
    "seize the command node before evacuation protocols complete",
    "hold the extraction route long enough for vital assets to escape",
    "destroy the enemy's forward augur array",
    "recover a sealed archive before orbital fire erases the site",
    "break through the cordon and silence the guns",
    "capture a ranking officer for interrogation",
    "deny the field by sabotaging fuel, munitions, and comms relays",
    "secure the landing zone for the next wave",
)

COMPLICATIONS = (
    "A damaged vox-net means orders arrive late, garbled, or through compromised channels.",
    "Civilian convoys, bonded auxilia, or refugee columns cross the battlefield at the worst moment.",
    "A third force has mined the approaches and left automated sentries active.",
    "Orbital debris begins falling across the field after turn two.",
    "Both sides are racing against a reactor overload that will make the centre ground lethal.",
    "A revered relic or disgraced banner is exposed in no man's land.",
    "The battlefield's machine-spirit rejects both forces and begins locking blast doors.",
    "Night fighting, ash fall, and false auspex returns make target priority unreliable.",
)

TONE_MODIFIERS = {
    "desperate": "Every order should feel costly, improvised, and close to collapse.",
    "grim": "Keep the briefing bleak, attritional, and focused on duty over survival.",
    "heroic": "Frame the battle around sacrifice, reputation, and a last chance to turn the war.",
    "horror": "Lean into isolation, corrupted signals, and the sense that something is wrong.",
    "siege": "Emphasise grinding pressure, breached walls, ammunition shortages, and stubborn defence.",
    "traitor": "Use mistrust, false colours, betrayal, and broken oaths as the main texture.",
}
 

class ScenarioSeedError(RuntimeError):
    """Raised when a scenario seed cannot be generated."""


@dataclass(frozen=True)
class SourceHit:
    label: str
    path: str
    excerpt: str
    score: int


@dataclass(frozen=True)
class ScenarioSeed:
    title: str
    attacker: str
    defender: str
    tone: str
    briefing: str
    attacker_objective: str
    defender_objective: str
    complication: str
    sources: list[SourceHit]


def generate_scenario_seed(
    summaries_dir: str,
    attacker: str,
    defender: str,
    tone: str | None = None,
    reroll: int = 0,
    mission_type: str | None = None,
    theatre: str | None = None,
) -> ScenarioSeed:
    clean_attacker = _clean_input(attacker, "attacker")
    clean_defender = _clean_input(defender, "defender")
    clean_tone = " ".join((tone or "grim").split()) or "grim"
    clean_mission_type = _clean_input(mission_type, "mission type") if mission_type is not None else None
    clean_theatre = _clean_input(theatre, "theatre") if theatre is not None else None
    root = Path(summaries_dir)

    if not root.exists():
        raise ScenarioSeedError(f"Summaries directory does not exist: {root}")

    markdown_files = sorted(path for path in root.rglob("*.md") if path.is_file())

    if not markdown_files:
        raise ScenarioSeedError(f"No Markdown summaries found in: {root}")

    attacker_hit = _best_source_hit(root, markdown_files, clean_attacker)
    defender_hit = _best_source_hit(root, markdown_files, clean_defender)
    seed_material = "|".join(
        (
            clean_attacker.lower(),
            clean_defender.lower(),
            clean_tone.lower(),
            attacker_hit.path if attacker_hit else "",
            defender_hit.path if defender_hit else "",
            str(reroll),
        )
    )
    rng = random.Random(_stable_seed(seed_material))
    chosen_mission_type = clean_mission_type if clean_mission_type is not None else rng.choice(MISSION_TYPES)
    chosen_theatre = clean_theatre if clean_theatre is not None else rng.choice(THEATRES)
    attacker_objective = rng.choice(OBJECTIVES)
    defender_objective = rng.choice([objective for objective in OBJECTIVES if objective != attacker_objective])
    complication = _tone_complication(clean_tone, rng)
    title = _build_title(clean_attacker, clean_defender, chosen_mission_type, rng)
    sources = _dedupe_hits([attacker_hit, defender_hit])
    source_clause = _source_clause(sources)
    briefing = (
        f"{clean_attacker} commit to a {chosen_mission_type} against {clean_defender} across {chosen_theatre}. "
        f"{source_clause} {TONE_MODIFIERS.get(clean_tone.lower(), f'Tone: {clean_tone}.')}"
    )

    return ScenarioSeed(
        title=title,
        attacker=clean_attacker,
        defender=clean_defender,
        tone=clean_tone,
        briefing=briefing,
        attacker_objective=attacker_objective,
        defender_objective=defender_objective,
        complication=complication,
        sources=sources,
    )


def format_scenario_seed(seed: ScenarioSeed) -> str:
    source_lines = "\n".join(
        f"- `{source.path}`: {source.excerpt}" for source in seed.sources
    )

    if not source_lines:
        source_lines = "- No direct summary match; seed uses the general Heresy campaign corpus."

    message = (
        f"**Scenario Seed: {escape_public_text(seed.title)}**\n"
        f"**Attacker:** {escape_public_text(seed.attacker)}\n"
        f"**Defender:** {escape_public_text(seed.defender)}\n"
        f"**Tone:** {escape_public_text(seed.tone)}\n\n"
        f"**Briefing:** {escape_public_text(seed.briefing)}\n\n"
        f"**Objectives**\n"
        f"- Attacker: {seed.attacker_objective}\n"
        f"- Defender: {seed.defender_objective}\n\n"
        f"**Complication:** {seed.complication}\n\n"
        f"**Source cues**\n"
        f"{source_lines}"
    )

    if len(message) > DISCORD_MESSAGE_LIMIT:
        return message[: DISCORD_MESSAGE_LIMIT - 3].rstrip() + "..."

    return message


def generate_scenario_twist(tone: str | None = None) -> str:
    """Return a random complication, optionally coloured by a known tone."""
    clean_tone = " ".join((tone or "").split()).lower()
    if clean_tone in TONE_MODIFIERS:
        return f"{random.choice(COMPLICATIONS)} {TONE_MODIFIERS[clean_tone]}"
    return random.choice(COMPLICATIONS)


def _clean_input(value: str, field_name: str) -> str:
    cleaned = " ".join(value.split())

    if not cleaned:
        raise ScenarioSeedError(f"Please provide a {field_name}.")

    return cleaned[:80]


def _best_source_hit(root: Path, markdown_files: list[Path], query: str) -> SourceHit | None:
    tokens = _tokens(query)

    if not tokens:
        return None

    best: SourceHit | None = None

    for path in markdown_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        haystack = f"{path.as_posix()} {text}".lower()
        score = sum(haystack.count(token) for token in tokens)

        if score == 0:
            continue

        relative_path = path.relative_to(root).as_posix()
        excerpt = _best_excerpt(text, tokens, MAX_EXCERPT_LENGTH)
        hit = SourceHit(
            label=query,
            path=relative_path,
            excerpt=excerpt,
            score=score,
        )

        if best is None or hit.score > best.score or (hit.score == best.score and hit.path < best.path):
            best = hit

    return best


def _stable_seed(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _tone_complication(tone: str, rng: random.Random) -> str:
    lowered_tone = tone.lower()

    if lowered_tone in TONE_MODIFIERS:
        return f"{rng.choice(COMPLICATIONS)} {TONE_MODIFIERS[lowered_tone]}"

    return rng.choice(COMPLICATIONS)


def _build_title(attacker: str, defender: str, mission_type: str, rng: random.Random) -> str:
    title_patterns = (
        "Ashes Over {place}",
        "The {attacker} Gambit",
        "{defender} at Bay",
        "The {mission} at {place}",
        "Last Orders at {place}",
    )
    places = ("Kallastin", "Vordrast", "Calth", "Isstvan", "Thramas", "Paramar", "Signus", "Prospero")
    pattern = rng.choice(title_patterns)
    return pattern.format(
        attacker=attacker,
        defender=defender,
        mission=mission_type.title(),
        place=rng.choice(places),
    )


def _source_clause(sources: list[SourceHit]) -> str:
    if not sources:
        return "No exact source match was found, so the seed uses the broader campaign archive."

    labels = ", ".join(source.label for source in sources)
    return f"Use the matched summary cues for {labels} as the scenario's anchor."


def _dedupe_hits(hits: list[SourceHit | None]) -> list[SourceHit]:
    deduped: list[SourceHit] = []
    seen_paths: set[str] = set()

    for hit in hits:
        if hit is None or hit.path in seen_paths:
            continue

        deduped.append(hit)
        seen_paths.add(hit.path)

    return deduped


