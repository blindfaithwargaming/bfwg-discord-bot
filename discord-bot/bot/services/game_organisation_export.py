from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import discord

from bot.config import Settings
from bot.services.club_night_forum import (
    ForumClubNightLookupError,
    collect_forum_threads,
)
from bot.services.club_night_scheduler import SchedulerTickResult
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT, truncate


SNAPSHOT_SCHEMA_VERSION = 1


async def collect_game_organisation_snapshot(
    bot_client: Any,
    channel_id: int,
    *,
    message_limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Raw snapshot of every thread in the game organisation forum.

    Full server history: no archived-thread limit and, by default, no
    per-thread message limit. The snapshot carries raw thread and message
    data only — faction interpretation is done by agents reading the
    exported file, keeping the bot LLM-free.
    """
    threads = await collect_forum_threads(bot_client, channel_id, archived_limit=None)

    seen_ids: set[int] = set()
    thread_payloads: list[dict[str, Any]] = []

    for thread in threads:
        thread_id = int(getattr(thread, "id", 0))
        if thread_id in seen_ids:
            continue
        seen_ids.add(thread_id)
        thread_payloads.append(await _snapshot_thread(thread, message_limit=message_limit))

    thread_payloads.sort(key=lambda payload: payload["created_at"] or "")

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "channel_id": channel_id,
        "thread_count": len(thread_payloads),
        "threads": thread_payloads,
    }


async def _snapshot_thread(thread: Any, *, message_limit: int | None) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    fetch_failed = False

    try:
        async for message in thread.history(limit=message_limit, oldest_first=True):
            messages.append(_snapshot_message(message))
    except (discord.Forbidden, discord.HTTPException) as exc:
        # One unreadable thread must not lose the rest of the history.
        fetch_failed = True
        logging.warning(
            "Could not read messages in game organisation thread %s: %s",
            getattr(thread, "id", "unknown"),
            exc,
        )

    return {
        "id": int(getattr(thread, "id", 0)),
        "title": str(getattr(thread, "name", "")),
        "created_at": _iso_or_none(getattr(thread, "created_at", None)),
        "archived": bool(getattr(thread, "archived", False)),
        "fetch_failed": fetch_failed,
        "message_count": len(messages),
        "messages_truncated": message_limit is not None and len(messages) >= message_limit,
        "messages": messages,
    }


def _snapshot_message(message: Any) -> dict[str, Any]:
    author = getattr(message, "author", None)
    display_name = getattr(author, "display_name", None) or getattr(author, "name", "")

    return {
        "author_id": int(getattr(author, "id", 0)),
        "author_display_name": str(display_name),
        "author_is_bot": bool(getattr(author, "bot", False)),
        "created_at": _iso_or_none(getattr(message, "created_at", None)),
        "content": truncate(str(getattr(message, "content", "") or ""), DISCORD_MESSAGE_LIMIT),
    }


def write_snapshot(snapshot: dict[str, Any], path: str | os.PathLike[str]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, sort_keys=True, indent=2)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def snapshot_generated_at(path: str | os.PathLike[str]) -> datetime | None:
    """`generated_at` of the existing snapshot, or None when unusable.

    Any unreadable, corrupt, or oddly-shaped file is treated as missing so the
    next tick simply re-exports over it.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    generated_at = data.get("generated_at")
    if not isinstance(generated_at, str):
        return None

    try:
        parsed = datetime.fromisoformat(generated_at)
    except ValueError:
        return None

    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def is_export_due(
    path: str | os.PathLike[str],
    *,
    now: datetime,
    max_age_hours: int,
) -> bool:
    generated_at = snapshot_generated_at(path)
    if generated_at is None:
        return True
    return now - generated_at >= timedelta(hours=max_age_hours)


async def run_game_organisation_export_tick(
    bot_client: Any,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> SchedulerTickResult:
    channel_id = settings.game_organisation_channel_id
    if channel_id is None:
        return SchedulerTickResult("skipped", "channel_unconfigured")

    target_now = now or datetime.now(UTC)

    if not is_export_due(
        settings.game_organisation_export_path,
        now=target_now,
        max_age_hours=settings.game_organisation_export_max_age_hours,
    ):
        return SchedulerTickResult("skipped", "snapshot_fresh")

    try:
        snapshot = await collect_game_organisation_snapshot(
            bot_client,
            channel_id,
            now=target_now,
        )
    except ForumClubNightLookupError:
        return SchedulerTickResult("failed", "forum_lookup_failed")

    try:
        write_snapshot(snapshot, settings.game_organisation_export_path)
    except OSError as exc:
        logging.warning(
            "Could not write game organisation snapshot to %s: %s",
            settings.game_organisation_export_path,
            exc,
        )
        return SchedulerTickResult("failed", "snapshot_write_failed")

    return SchedulerTickResult("exported", f"threads={snapshot['thread_count']}")


def _iso_or_none(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
