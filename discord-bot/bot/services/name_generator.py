import json
import random
import re
from pathlib import Path

from bot.services.text_utils import DISCORD_MESSAGE_LIMIT, truncate

CATEGORIES: tuple[str, ...] = ("commander", "ship", "warband", "relic", "location")

_DATA_DIR = Path(__file__).parent.parent / "data" / "name_styles"

# Canonical faction names — used for case-insensitive resolution.
# Adding a new faction requires: a matching JSON file in _DATA_DIR and an entry here.
FACTION_NAMES: tuple[str, ...] = (
    "Adeptus Custodes",
    "Alpha Legion",
    "Blackshields",
    "Blood Angels",
    "Dark Angels",
    "Daemonic Forces",
    "Death Guard",
    "Emperor's Children",
    "Imperial Fists",
    "Iron Hands",
    "Iron Warriors",
    "Knights-Errant",
    "Mechanicum",
    "Night Lords",
    "Questoris Knights",
    "Raven Guard",
    "Salamanders",
    "Shattered Legions",
    "Sisters of Silence",
    "Solar Auxilia",
    "Sons of Horus",
    "Space Wolves",
    "Thousand Sons",
    "Ultramarines",
    "White Scars",
    "Word Bearers",
    "World Eaters",
)

_WEAPON_TYPES: list[str] = [
    "Axe", "Blade", "Bolter", "Banner", "Gauntlet", "Hammer", "Shield", "Spear",
]

_cache: dict[str, dict] = {}


class NameGeneratorError(RuntimeError):
    pass


def generate_names(
    faction: str,
    category: str,
    count: int = 5,
    seed: int | None = None,
) -> list[str]:
    resolved = _resolve_faction(faction)
    if category not in CATEGORIES:
        raise NameGeneratorError(
            f"Unknown category {category!r}. Valid categories: {', '.join(CATEGORIES)}."
        )
    style = _load_style(resolved)
    rng = random.Random(seed)

    seen: set[str] = set()
    results: list[str] = []
    for _ in range(count * 20):
        if len(results) >= count:
            break
        name = _make_name(category, style, rng)
        if name not in seen:
            seen.add(name)
            results.append(name)

    return results


def format_names(faction: str, category: str, names: list[str]) -> str:
    header = f"**{faction} — {category.title()} Names**"
    lines = "\n".join(f"• {name}" for name in names)
    return truncate(f"{header}\n{lines}", DISCORD_MESSAGE_LIMIT)


def reset() -> None:
    _cache.clear()


def _load_style(faction: str) -> dict:
    if faction in _cache:
        return _cache[faction]
    path = _DATA_DIR / _faction_to_filename(faction)
    data = json.loads(path.read_text(encoding="utf-8"))
    _cache[faction] = data
    return data


def _faction_to_filename(faction: str) -> str:
    sanitised = re.sub(r"[^a-z0-9]+", "_", faction.lower().replace("'", ""))
    return sanitised.strip("_") + ".json"


def _resolve_faction(name: str) -> str:
    stripped = name.strip()
    if stripped in FACTION_NAMES:
        return stripped
    lower = stripped.lower()
    for faction in FACTION_NAMES:
        if faction.lower() == lower:
            return faction
    raise NameGeneratorError(
        f"Unknown faction {stripped!r}. Use faction autocomplete or check your spelling."
    )


def _make_name(category: str, style: dict, rng: random.Random) -> str:
    if category == "commander":
        return _make_commander(style, rng)
    if category == "ship":
        return _make_ship(style, rng)
    if category == "warband":
        return _make_warband(style, rng)
    if category == "relic":
        return _make_relic(style, rng)
    return _make_location(style, rng)


def _make_commander(style: dict, rng: random.Random) -> str:
    given = rng.choice(style["given"])
    p = rng.randint(0, 2)
    if p == 0:
        return f"{rng.choice(style['titles'])} {given}"
    if p == 1:
        return f"{given} {rng.choice(style['epithets'])}"
    return f"{given} the {rng.choice(style['epithets'])}"


def _make_ship(style: dict, rng: random.Random) -> str:
    adj = rng.choice(style["adj"])
    noun = rng.choice(style["nouns"])
    setting = rng.choice(style["setting"])
    p = rng.randint(0, 2)
    if p == 0:
        return f"The {adj} {noun}"
    if p == 1:
        return f"{noun} of {setting}"
    return f"The {adj} {noun} of {setting}"


def _make_warband(style: dict, rng: random.Random) -> str:
    collective = rng.choice(style["collective"])
    setting = rng.choice(style["setting"])
    adj = rng.choice(style["adj"])
    p = rng.randint(0, 3)
    if p == 0:
        return f"The {adj} {collective}"
    if p == 1:
        return f"Sons of {setting}"
    if p == 2:
        return f"{collective} of {setting}"
    return f"The {collective}"


def _make_relic(style: dict, rng: random.Random) -> str:
    weapon = rng.choice(style.get("relic_nouns", _WEAPON_TYPES))
    setting = rng.choice(style["setting"])
    adj = rng.choice(style["adj"])
    p = rng.randint(0, 2)
    if p == 0:
        return f"The {adj} {weapon}"
    if p == 1:
        return f"{setting}'s {weapon}"
    return f"The {weapon} of {setting}"


def _make_location(style: dict, rng: random.Random) -> str:
    place = rng.choice(style["place_types"])
    setting = rng.choice(style["setting"])
    adj = rng.choice(style["adj"])
    p = rng.randint(0, 2)
    if p == 0:
        return f"The {adj} {place}"
    if p == 1:
        return f"{place} of {setting}"
    return f"{adj} {place}"
