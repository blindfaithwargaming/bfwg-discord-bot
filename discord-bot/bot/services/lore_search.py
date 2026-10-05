from dataclasses import dataclass
from pathlib import Path

from bot.services.discord_text import escape_public_text
from bot.services.lore_corpus import is_excluded_from_lore_search
from bot.services.text_utils import (
    DISCORD_MESSAGE_LIMIT,
    best_excerpt as _best_excerpt,
    normalize_markdown as _normalize_markdown,
    tokens as _tokens,
    truncate as _truncate,
)


MAX_EXCERPT_LENGTH = 260
DEFAULT_RESULT_LIMIT = 5


class LoreSearchError(RuntimeError):
    """Raised when lore summaries cannot be searched."""


@dataclass(frozen=True)
class LoreSearchHit:
    path: str
    excerpt: str
    score: int


def search_lore(
    summaries_dir: str,
    query: str,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> list[LoreSearchHit]:
    clean_query = _clean_query(query)
    root = Path(summaries_dir)

    if not root.exists():
        raise LoreSearchError(f"Summaries directory does not exist: {root}")

    markdown_files = _markdown_files(root)

    if not markdown_files:
        raise LoreSearchError(f"No Markdown summaries found in: {root}")

    tokens = _tokens(clean_query)

    if not tokens:
        raise LoreSearchError("Please provide a more specific search query.")

    hits: list[LoreSearchHit] = []

    for path in markdown_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        relative_path = path.relative_to(root).as_posix()
        score = _score_text(relative_path, text, tokens)

        if score <= 0:
            continue

        hits.append(
            LoreSearchHit(
                path=relative_path,
                excerpt=_best_excerpt(text, tokens, MAX_EXCERPT_LENGTH),
                score=score,
            )
        )

    hits.sort(key=lambda hit: (-hit.score, hit.path))
    return hits[: max(1, limit)]


def format_lore_search(query: str, hits: list[LoreSearchHit]) -> str:
    clean_query = escape_public_text(_clean_query(query))

    if not hits:
        return f"**Lore search:** {clean_query}\nNo matching summaries found."

    lines = [
        f"**Lore search:** {clean_query}",
        "",
        *[
            f"{index}. `{hit.path}`\n{hit.excerpt}"
            for index, hit in enumerate(hits, start=1)
        ],
    ]
    return _truncate("\n\n".join(lines), DISCORD_MESSAGE_LIMIT)


def format_source_results(query: str, hits: list[LoreSearchHit]) -> str:
    clean_query = escape_public_text(_clean_query(query))

    if not hits:
        return f"**Source lookup:** {clean_query}\nNo matching summary files found."

    lines = [
        f"**Source lookup:** {clean_query}",
        "",
        *[
            f"{index}. `{hit.path}` - {hit.excerpt}"
            for index, hit in enumerate(hits, start=1)
        ],
    ]
    return _truncate("\n".join(lines), DISCORD_MESSAGE_LIMIT)


def _markdown_files(root: Path) -> list[Path]:
    all_markdown_files = sorted(
        path
        for path in root.rglob("*.md")
        if path.is_file() and not is_excluded_from_lore_search(root, path)
    )
    return [
        path for path in all_markdown_files if path.name.lower() != "readme.md"
    ] or all_markdown_files


def _clean_query(value: str) -> str:
    cleaned = " ".join(value.split())

    if not cleaned:
        raise LoreSearchError("Please provide a search query.")

    return cleaned[:120]


def _score_text(path: str, text: str, tokens: set[str]) -> int:
    normalized_path = path.lower()
    normalized_text = _normalize_markdown(text).lower()
    score = 0

    for token in tokens:
        score += normalized_text.count(token)

        if token in normalized_path:
            score += 3

    if all(token in f"{normalized_path} {normalized_text}" for token in tokens):
        score += 5

    return score


