import re
from pathlib import Path

_EXCLUDED_TOPICS: frozenset[str] = frozenset({
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
    "The Warrior Lodge System",
    "Daemonic and Ritual War",
    "Red Thirst and Signus Temptation",
    "Traitor Legions and Dark Compliance",
    "Traitor Legions and their Allies",
    "Loyalist Legions",
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
    "Council of Nikaea and the Censure",
    "The Burning of Prospero",
    "The Aether and Psychic Catastrophe",
    "The Thramas Crusade",
    "Night Lords Terror War",
    "Dark Angels Counter-Crusade",
    "The Heavy Toll of Victory",
    "Principal Worlds of the Eastern Fringe",
    "Thramas Mortal Forces",
    "Thramas Strategic Context",
    "The Wars of the Shattered Legions",
    "Word Bearers Betrayal",
})

# Compound topics mapped to the canonical faction name that should appear in autocomplete.
# Mechanicum already has its own dedicated file, so "Questoris Knights and Mechanicum"
# normalises to just "Questoris Knights" — both appear as separate index entries.
# Book 4 (Conquest) splits the Questoris Knights into one file per Household; each
# Household topic rolls up to the "Questoris Knights" umbrella faction, and the
# "Mechanicum and Titans" topic folds into the existing "Mechanicum" faction.
_TOPIC_NORMALISATIONS: dict[str, str] = {
    "White Scars and Chondax": "White Scars",
    "Questoris Knights and Mechanicum": "Questoris Knights",
    "Knights-Errant and Crusader Host": "Knights-Errant",
    "House Makabius": "Questoris Knights",
    "House Vyronii": "Questoris Knights",
    "House Orhlacc": "Questoris Knights",
    "House Ærthegn": "Questoris Knights",
    "House Vornherr": "Questoris Knights",
    "Mechanicum and Titans": "Mechanicum",
    "Mechanicum of Xana and House Malinax": "Mechanicum",
    "Mechanicum Taghmata": "Mechanicum",
    "Thramassi Nightwatch and Solar Auxilia": "Solar Auxilia",
    "Triplex Forge Worlds and Mechanicum": "Mechanicum",
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_TOPIC_RE = re.compile(r'^topic:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)

_cache: dict[str, list[str]] = {}


def get_faction_names(summaries_dir: str) -> list[str]:
    """Return a sorted, deduplicated list of faction names extracted from summaries frontmatter."""
    if summaries_dir in _cache:
        return _cache[summaries_dir]

    root = Path(summaries_dir)
    names: set[str] = set()

    if root.exists():
        for path in sorted(root.rglob("*.md")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue

            topic = _extract_topic(text)
            if topic is None:
                continue

            normalised = _normalise_topic(topic)
            if normalised is None:
                continue

            names.add(normalised)

    result = sorted(names)
    _cache[summaries_dir] = result
    return result


def autocomplete_faction(summaries_dir: str, query: str) -> list[str]:
    """Return up to 25 faction names matching query as a case-insensitive substring."""
    names = get_faction_names(summaries_dir)
    q = query.strip().lower()

    if not q:
        return names[:25]

    return [name for name in names if q in name.lower()][:25]


def reset() -> None:
    _cache.clear()


def _extract_topic(text: str) -> str | None:
    fm_match = _FRONTMATTER_RE.match(text)
    if not fm_match:
        return None

    topic_match = _TOPIC_RE.search(fm_match.group(1))
    if not topic_match:
        return None

    return topic_match.group(1).strip()


def _normalise_topic(topic: str) -> str | None:
    if topic in _EXCLUDED_TOPICS:
        return None

    if (
        topic.endswith("complete summary")
        or topic.endswith("expanded file index")
        or topic.endswith("conversion progress")
    ):
        return None

    return _TOPIC_NORMALISATIONS.get(topic, topic)
