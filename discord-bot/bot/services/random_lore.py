import random
from dataclasses import dataclass
from pathlib import Path

from bot.services.discord_text import escape_public_text
from bot.services.lore_corpus import is_excluded_from_random_lore
from bot.services.text_utils import (
    DISCORD_MESSAGE_LIMIT,
    SENTENCE_PATTERN,
    normalize_markdown as _normalize_markdown,
    tokens as _tokens,
    truncate as _truncate,
)


MAX_EXCERPT_LENGTH = 320
DEFAULT_FOCUS = "General archive"

CAMPAIGN_PROMPTS = (
    "Use this as the opening incident for a raid, recovery mission, or punitive strike.",
    "Frame the next battle around whether this fragment becomes public, is buried, or is weaponised.",
    "Make this the reason two commanders commit forces before their wider campaign plans are ready.",
    "Turn this into a contested objective that both sides understand differently.",
    "Let this cue define the battlefield's mood, command friction, and victory conditions.",
)

TABLE_HOOKS = (
    "Place a data-cache, wounded officer, relic marker, or compromised vox-node near the centre of the table.",
    "Give one player a secret extraction objective and the other a denial objective.",
    "Start with unreliable reserves, night fighting, blocked comms, or a collapsing flank.",
    "Make the source cue a terrain rule: sealed blast doors, burning wreckage, rad zones, or failing shields.",
    "Add a third-party hazard such as automated sentries, panicked auxilia, or an incoming bombardment.",
)

TITLE_PATTERNS = (
    "Archive Fragment: {subject}",
    "Campaign Hook: {subject}",
    "Recovered Briefing: {subject}",
    "Lore Seed: {subject}",
)


class RandomLoreError(RuntimeError):
    """Raised when a random lore prompt cannot be generated."""


@dataclass(frozen=True)
class RandomLore:
    title: str
    focus: str
    source_path: str
    excerpt: str
    campaign_prompt: str
    table_hook: str


def generate_random_lore(
    summaries_dir: str,
    faction: str | None = None,
    rng: random.Random | None = None,
) -> RandomLore:
    root = Path(summaries_dir)

    if not root.exists():
        raise RandomLoreError(f"Summaries directory does not exist: {root}")

    all_markdown_files = sorted(path for path in root.rglob("*.md") if path.is_file())
    markdown_files = [
        path for path in all_markdown_files if path.name.lower() != "readme.md"
    ] or all_markdown_files
    markdown_files = [
        path for path in markdown_files if not is_excluded_from_random_lore(root, path)
    ]

    if not markdown_files:
        raise RandomLoreError(f"No Markdown summaries found in: {root}")

    clean_focus = _clean_optional_input(faction)
    tokens = _tokens(clean_focus) if clean_focus else set()
    token_matches, entries = _collect_entries(root, markdown_files, tokens)

    if tokens and not token_matches:
        raise RandomLoreError(f"No Markdown summaries matched faction or focus: {clean_focus}")

    if not entries:
        raise RandomLoreError(f"No readable Markdown summaries found in: {root}")

    lore_rng = rng or random.SystemRandom()
    source_path, excerpts = lore_rng.choice(entries)
    excerpt = lore_rng.choice(excerpts)
    subject = clean_focus or _source_subject(source_path)
    title = lore_rng.choice(TITLE_PATTERNS).format(subject=subject)

    return RandomLore(
        title=title,
        focus=clean_focus or DEFAULT_FOCUS,
        source_path=source_path,
        excerpt=excerpt,
        campaign_prompt=lore_rng.choice(CAMPAIGN_PROMPTS),
        table_hook=lore_rng.choice(TABLE_HOOKS),
    )


def format_random_lore(lore: RandomLore) -> str:
    message = (
        f"**{escape_public_text(lore.title)}**\n"
        f"**Focus:** {escape_public_text(lore.focus)}\n"
        f"**Source:** `{lore.source_path}`\n\n"
        f"**Lore cue:** {lore.excerpt}\n\n"
        f"**Campaign prompt:** {lore.campaign_prompt}\n\n"
        f"**Table hook:** {lore.table_hook}"
    )

    return _truncate(message, DISCORD_MESSAGE_LIMIT)


def _clean_optional_input(value: str | None) -> str:
    cleaned = " ".join((value or "").split())
    return cleaned[:80]


def _collect_entries(
    root: Path,
    markdown_files: list[Path],
    tokens: set[str],
) -> tuple[int, list[tuple[str, list[str]]]]:
    entries: list[tuple[str, list[str]]] = []
    token_matches = 0

    for path in markdown_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        if tokens:
            haystack = f"{path.as_posix()} {text}".lower()
            if not all(token in haystack for token in tokens):
                continue
            token_matches += 1

        excerpts = _candidate_excerpts(text, tokens)

        if not excerpts:
            continue

        entries.append((path.relative_to(root).as_posix(), excerpts))

    return token_matches, entries


def _candidate_excerpts(text: str, tokens: set[str]) -> list[str]:
    normalized_text = _normalize_markdown(text)
    sentences = [
        sentence.strip()
        for sentence in SENTENCE_PATTERN.findall(normalized_text)
        if len(sentence.strip()) >= 40
    ]

    if tokens:
        focused_sentences = [
            sentence
            for sentence in sentences
            if any(token in sentence.lower() for token in tokens)
        ]

        if focused_sentences:
            return [_truncate(sentence, MAX_EXCERPT_LENGTH) for sentence in focused_sentences]

    if sentences:
        return [_truncate(sentence, MAX_EXCERPT_LENGTH) for sentence in sentences]

    if normalized_text:
        return [_truncate(normalized_text, MAX_EXCERPT_LENGTH)]

    return []


def _source_subject(source_path: str) -> str:
    return Path(source_path).stem.replace("_", " ").replace("-", " ").title()
