from pathlib import Path


RANDOM_LORE_EXCLUDED_TOP_LEVEL_DIRS = frozenset({"rules", "missions"})
LORE_SEARCH_EXCLUDED_TOP_LEVEL_DIRS = frozenset({"missions"})
BOT_EXCLUDED_DIR_NAMES = frozenset({"agent"})


def is_excluded_from_random_lore(root: Path, path: Path) -> bool:
    """True if ``path`` lives under a non-lore top-level corpus directory.

    Random lore should never sample rules text or mission objectives; those
    belong behind ``/rules query`` and ``/mission random`` instead. Agent-only
    context summaries are also hidden from bot responses.
    """
    if _is_under_bot_excluded_dir(root, path):
        return True

    return _is_under_excluded_top_level_dir(
        root,
        path,
        RANDOM_LORE_EXCLUDED_TOP_LEVEL_DIRS,
    )


def is_excluded_from_lore_search(root: Path, path: Path) -> bool:
    """True if ``path`` should be hidden from source/search-style lore lookup."""
    if _is_under_bot_excluded_dir(root, path):
        return True

    return _is_under_excluded_top_level_dir(
        root,
        path,
        LORE_SEARCH_EXCLUDED_TOP_LEVEL_DIRS,
    )


def _is_under_excluded_top_level_dir(
    root: Path,
    path: Path,
    excluded_top_level_dirs: frozenset[str],
) -> bool:
    relative_parts = path.relative_to(root).parts
    return bool(relative_parts) and relative_parts[0].lower() in excluded_top_level_dirs


def _is_under_bot_excluded_dir(root: Path, path: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    return any(part.lower() in BOT_EXCLUDED_DIR_NAMES for part in relative_parts[:-1])
