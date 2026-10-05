import re
from dataclasses import dataclass
from pathlib import Path

from bot.services.discord_text import escape_public_text
from bot.services.text_utils import (
    DISCORD_MESSAGE_LIMIT,
    normalize_markdown as _normalize_markdown,
    tokens as _tokens,
    truncate as _truncate,
)


TOP_EXCERPT_LENGTH = 600
DEFAULT_RESULT_LIMIT = 4
KEYWORD_BONUS = 4
TOPIC_BONUS = 2

_LIST_RE = re.compile(r"^\[(.*)\]$")
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")


class RulesQueryError(RuntimeError):
    """Raised when the rules corpus cannot be searched."""


@dataclass(frozen=True)
class RulesQueryHit:
    path: str
    title: str
    excerpt: str
    score: int
    source_book: str
    source_section: str
    source_url: str


def search_rules(
    summaries_dir: str,
    query: str,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> list[RulesQueryHit]:
    clean_query = _clean_query(query)
    rules_root = Path(summaries_dir) / "rules"

    if not rules_root.exists():
        raise RulesQueryError(
            "The rules corpus is not available yet. Please try again later."
        )

    markdown_files = _markdown_files(rules_root)

    if not markdown_files:
        raise RulesQueryError(
            "The rules corpus is empty. Please try again later."
        )

    query_tokens = _tokens(clean_query)

    if not query_tokens:
        raise RulesQueryError("Please provide a more specific rules query.")

    hits: list[RulesQueryHit] = []

    for path in markdown_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        frontmatter, body = _split_frontmatter(text)
        relative_path = path.relative_to(rules_root).as_posix()
        score = _score_text(relative_path, body, query_tokens)
        score += _frontmatter_bonus(frontmatter, query_tokens)

        if score <= 0:
            continue

        hits.append(
            RulesQueryHit(
                path=relative_path,
                title=_extract_title(body, frontmatter),
                excerpt=_best_rules_excerpt(
                    _body_without_title(body),
                    query_tokens,
                    TOP_EXCERPT_LENGTH,
                ),
                score=score,
                source_book=frontmatter.get("source_book", ""),
                source_section=frontmatter.get("source_section", ""),
                source_url=frontmatter.get("source_url", ""),
            )
        )

    hits.sort(key=lambda hit: (-hit.score, hit.path))
    return hits[: max(1, limit)]


def format_rules_query(query: str, hits: list[RulesQueryHit]) -> str:
    clean_query = escape_public_text(_clean_query(query))

    if not hits:
        message = (
            f"**Rules query:** {clean_query}\n"
            "No matching rules found. Try rephrasing with different terms — "
            "the rules corpus is still growing."
        )
        return _truncate(message, DISCORD_MESSAGE_LIMIT)

    top = hits[0]
    lines = [format_rules_hit(query, top)]

    further = hits[1:]
    if further:
        lines.append("")
        lines.append("More results:")
        lines.extend(
            f"{index}. `{hit.path}` — {_short_citation(hit)}"
            for index, hit in enumerate(further, start=2)
        )

    return _truncate("\n".join(lines), DISCORD_MESSAGE_LIMIT)


def format_rules_hit(query: str, hit: RulesQueryHit) -> str:
    clean_query = escape_public_text(_clean_query(query))
    message = "\n".join(
        [
            f"**Rules query:** {clean_query}",
            "",
            f"**{hit.title}**",
            hit.excerpt,
            _citation_line(hit),
        ]
    )
    return _truncate(message, DISCORD_MESSAGE_LIMIT)


def result_option_label(hit: RulesQueryHit) -> str:
    return _truncate(hit.title or hit.path, 100)


def result_option_description(hit: RulesQueryHit) -> str:
    return _truncate(_short_citation(hit), 100)


def _citation_line(hit: RulesQueryHit) -> str:
    citation = f"Source: {hit.source_book} — {hit.source_section}".rstrip(" —")
    if hit.source_url:
        citation += f"\n<{hit.source_url}>"
    return citation


def _short_citation(hit: RulesQueryHit) -> str:
    if hit.source_section:
        return f"{hit.source_book} ({hit.source_section})"
    return hit.source_book or hit.title


def _markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if path.is_file() and path.name.lower() != "readme.md"
    )


def _clean_query(value: str) -> str:
    cleaned = " ".join(value.split())

    if not cleaned:
        raise RulesQueryError("Please provide a rules query.")

    return cleaned[:120]


def _score_text(path: str, text: str, query_tokens: set[str]) -> int:
    normalized_path = path.lower()
    normalized_text = _normalize_markdown(text).lower()
    score = 0

    for token in query_tokens:
        score += normalized_text.count(token)

        if token in normalized_path:
            score += 3

    if all(token in f"{normalized_path} {normalized_text}" for token in query_tokens):
        score += 5

    return score


def _frontmatter_bonus(frontmatter: dict[str, str], query_tokens: set[str]) -> int:
    keywords_text = " ".join(_parse_list(frontmatter.get("keywords", ""))).lower()
    topic_text = frontmatter.get("rules_topic", "").lower()
    bonus = 0

    for token in query_tokens:
        if token in keywords_text:
            bonus += KEYWORD_BONUS
        if token in topic_text:
            bonus += TOPIC_BONUS

    return bonus


def _extract_title(body: str, frontmatter: dict[str, str]) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return frontmatter.get("rules_topic", "Rules")


def _body_without_title(body: str) -> str:
    lines = body.splitlines()

    for index, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join(lines[index + 1:]).lstrip("\n")

    return body


def _best_rules_excerpt(text: str, query_tokens: set[str], max_length: int) -> str:
    normalized = _normalize_rules_excerpt_text(text)
    sentences = [s.strip() for s in _SENTENCE_RE.findall(normalized) if s.strip()]
    best = ""
    best_score = -1

    for sentence in sentences:
        lowered = sentence.lower()
        score = sum(lowered.count(token) for token in query_tokens)

        if score > best_score:
            best = sentence
            best_score = score

    if not best:
        best = normalized[:max_length]

    excerpt = _truncate(best, max_length)
    return _strip_unbalanced_inline_markers(excerpt)


def _normalize_rules_excerpt_text(text: str) -> str:
    without_links = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    without_code = re.sub(r"`([^`]+)`", r"\1", without_links)
    without_headings = re.sub(r"^#{1,6}\s+", "", without_code, flags=re.MULTILINE)
    without_blockquotes = re.sub(r"^>\s?", "", without_headings, flags=re.MULTILINE)
    without_bullets = re.sub(r"^\s*(?:[-+]|\d+\.)\s+", "", without_blockquotes, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", without_bullets).strip()


def _strip_unbalanced_inline_markers(value: str) -> str:
    for marker in ("**", "__", "`"):
        if value.count(marker) % 2:
            value = value.replace(marker, "")
    return value


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
            value = re.sub(r"\s+#.*$", "", value).strip()
            result[key.strip()] = value

    return result, body


def _parse_list(value: str) -> list[str]:
    stripped = value.strip()
    match = _LIST_RE.match(stripped)

    if not match:
        return [stripped] if stripped else []

    inner = match.group(1).strip()

    if not inner:
        return []

    return [item.strip() for item in inner.split(",") if item.strip()]
