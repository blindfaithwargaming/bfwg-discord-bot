import re
import unicodedata

DISCORD_MESSAGE_LIMIT = 2000
TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9'-]{2,}", re.IGNORECASE)
SENTENCE_PATTERN = re.compile(r"[^.!?\n]+[.!?]?")


def normalize_markdown(text: str) -> str:
    without_links = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    without_code = re.sub(r"`([^`]+)`", r"\1", without_links)
    without_markers = re.sub(r"^[#>*\-\s]+", "", without_code, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", without_markers).strip()


def tokens(value: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(value)}


def clean_user_text(value: str) -> str:
    """Strip control/format characters and collapse whitespace.

    Used on user-supplied text before it is persisted to state files that are
    later read by out-of-bot agent commands: control and format characters
    (zero-width joiners, bidi overrides, NULs) are dropped so stored data
    cannot smuggle invisible content past a reviewer.
    """
    stripped = "".join(
        ch for ch in value if ch.isspace() or not unicodedata.category(ch).startswith("C")
    )
    return " ".join(stripped.split())


def truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value

    return value[: max_length - 3].rstrip() + "..."


def best_excerpt(text: str, query_tokens: set[str], max_length: int) -> str:
    normalized = normalize_markdown(text)
    sentences = [s.strip() for s in SENTENCE_PATTERN.findall(normalized) if s.strip()]
    best = ""
    best_score = -1

    for sentence in sentences:
        lowered = sentence.lower()
        score = sum(lowered.count(t) for t in query_tokens)

        if score > best_score:
            best = sentence
            best_score = score

    if not best:
        best = normalized[:max_length]

    return truncate(best, max_length)
