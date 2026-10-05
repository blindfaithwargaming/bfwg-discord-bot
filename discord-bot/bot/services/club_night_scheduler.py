from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord

from bot.config import Settings
from bot.services.club_night_announcement import (
    AnnouncementTemplateError,
    ClubNightAnnouncementError,
    ReminderTemplateError,
    build_announcement_title,
    get_applied_tags,
    get_stadium_status_lines,
    append_stadium_status,
    load_announcement_body,
)
from bot.services.club_night_forum import (
    ForumClubNightLookupError,
    collect_forum_threads,
    find_latest_announcement_club_night,
    get_next_forum_club_night,
)
from bot.services.club_night_reminder import build_club_night_reminder_text
from bot.services.ids import validate_guild_id
from bot.services.stadium_events import StadiumEventsError, get_next_club_night


CLUB_NIGHT_REMINDER_PREFIX = "club-night-reminder"
CLUB_NIGHT_ANNOUNCEMENT_PREFIX = "club-night-announcement"
MAX_REMINDER_ATTEMPTS = 3
NO_MENTIONS = discord.AllowedMentions.none()


@dataclass(frozen=True)
class ScheduledPostClaim:
    claimed: bool
    post_id: str
    reason: str
    attempt_count: int


@dataclass(frozen=True)
class SchedulerTickResult:
    status: str
    reason: str
    post_id: str | None = None
    message_id: int | None = None


def scheduled_post_id(event_date: date, *, prefix: str = CLUB_NIGHT_REMINDER_PREFIX) -> str:
    return f"{prefix}-{event_date.isoformat()}"


def is_reminder_due(
    event_date: date,
    now: datetime,
    *,
    days_before: int,
    reminder_time: dt_time,
    timezone: str,
) -> bool:
    local_now = _local_datetime(now, timezone)
    due_date = event_date - timedelta(days=days_before)

    return local_now.date() == due_date and local_now.time() >= reminder_time


def is_announcement_due(
    previous_event_date: date | None,
    now: datetime,
    *,
    post_time: dt_time,
    timezone: str,
) -> bool:
    """Whether the next club-night announcement should be posted.

    Due from ``post_time`` on the day after the previous club night onwards, so
    a missed tick (bot down, Battlefields slow to list the next event) is
    caught up at the next opportunity rather than skipped for the cycle.
    """
    local_now = _local_datetime(now, timezone)

    if previous_event_date is None:
        return local_now.time() >= post_time

    if previous_event_date >= local_now.date():
        return False

    due_at = datetime.combine(
        previous_event_date + timedelta(days=1),
        post_time,
        tzinfo=local_now.tzinfo,
    )
    return local_now >= due_at


class MarkerStore:
    """Local JSON marker store for scheduled-post duplicate protection.

    The bot runs as a single instance (see design rules), so a local file is
    sufficient for "did I already post this reminder?" — no distributed lock is
    needed. State is keyed by ``guild_id`` then ``post_id`` and persisted as JSON.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logging.warning(
                "Reminder marker store %s is corrupt; treating as empty.", self._path
            )
            return {}
        if not isinstance(data, dict):
            return {}

        # Drop guild entries that are not marker mappings so a partially corrupt
        # file degrades to "not yet posted" instead of crashing get/put.
        cleaned = {
            guild_id: markers
            for guild_id, markers in data.items()
            if isinstance(markers, dict)
        }
        if len(cleaned) != len(data):
            logging.warning(
                "Reminder marker store %s has malformed guild entries; ignoring them.",
                self._path,
            )
        return cleaned

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=self._path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, sort_keys=True, indent=2)
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    def get(self, guild_id: str, post_id: str) -> dict[str, Any] | None:
        return self._load().get(guild_id, {}).get(post_id)

    def put(self, guild_id: str, post_id: str, marker: dict[str, Any]) -> None:
        data = self._load()
        data.setdefault(guild_id, {})[post_id] = marker
        self._save(data)


def _iso(value: datetime) -> str:
    return value.isoformat()


def claim_scheduled_post_marker(
    store: MarkerStore,
    guild_id: str,
    event_date: date,
    *,
    kind: str = CLUB_NIGHT_REMINDER_PREFIX,
    channel_id: int,
    now: datetime,
    source_service: str,
    git_commit: str,
    max_attempts: int = MAX_REMINDER_ATTEMPTS,
) -> ScheduledPostClaim:
    validated_guild_id = validate_guild_id(guild_id)
    post_id = scheduled_post_id(event_date, prefix=kind)
    existing = store.get(validated_guild_id, post_id)

    if existing is not None:
        status = existing.get("status")
        attempt_count = int(existing.get("attempt_count") or 0)

        if status == "posted":
            return ScheduledPostClaim(False, post_id, "already_posted", attempt_count)

        if attempt_count >= max_attempts:
            return ScheduledPostClaim(False, post_id, "attempts_exhausted", attempt_count)

        next_attempt = attempt_count + 1
        marker = {
            **existing,
            "status": "pending",
            "attempt_count": next_attempt,
            "last_attempt_at": _iso(now),
            "updated_at": _iso(now),
            "channel_id": str(channel_id),
            "source_service": source_service,
            "git_commit": git_commit,
        }
        store.put(validated_guild_id, post_id, marker)
        return ScheduledPostClaim(True, post_id, "claimed", next_attempt)

    marker = {
        "schema_version": 1,
        "post_id": post_id,
        "guild_id": validated_guild_id,
        "status": "pending",
        "kind": kind,
        "club_night_date": event_date.isoformat(),
        "channel_id": str(channel_id),
        "message_id": None,
        "attempt_count": 1,
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "last_attempt_at": _iso(now),
        "source_service": source_service,
        "git_commit": git_commit,
    }
    store.put(validated_guild_id, post_id, marker)
    return ScheduledPostClaim(True, post_id, "claimed", 1)


def mark_scheduled_post_posted(
    store: MarkerStore,
    guild_id: str,
    post_id: str,
    *,
    message_id: int,
    now: datetime,
) -> None:
    validated_guild_id = validate_guild_id(guild_id)
    existing = store.get(validated_guild_id, post_id) or {}
    store.put(
        validated_guild_id,
        post_id,
        {
            **existing,
            "status": "posted",
            "message_id": str(message_id),
            "posted_at": _iso(now),
            "updated_at": _iso(now),
            "failure_reason": None,
        },
    )


def mark_scheduled_post_failed(
    store: MarkerStore,
    guild_id: str,
    post_id: str,
    *,
    error: BaseException,
    now: datetime,
) -> None:
    validated_guild_id = validate_guild_id(guild_id)
    existing = store.get(validated_guild_id, post_id) or {}
    store.put(
        validated_guild_id,
        post_id,
        {
            **existing,
            "status": "failed",
            "failure_reason": _truncate_failure(str(error)),
            "updated_at": _iso(now),
        },
    )


async def run_club_night_reminder_tick(
    bot_client: Any,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> SchedulerTickResult:
    guild_id = _resolve_guild_id(bot_client, settings)
    if guild_id is None:
        return SchedulerTickResult("skipped", "guild_unavailable")

    channel_id = settings.club_night_reminder_channel_id
    if channel_id is None:
        return SchedulerTickResult("skipped", "channel_unconfigured")

    target_now = now or datetime.now(UTC)
    local_now = _local_datetime(target_now, settings.club_night_reminder_timezone)

    try:
        club_night = await get_next_forum_club_night(
            bot_client,
            settings.club_night_forum_channel_id,
            today=local_now.date(),
        )
    except ForumClubNightLookupError:
        return SchedulerTickResult("failed", "club_night_forum_lookup_failed")
    if club_night is None:
        return SchedulerTickResult("skipped", "club_night_forum_post_not_found")

    if not is_reminder_due(
        club_night.event_date,
        target_now,
        days_before=settings.club_night_reminder_days_before,
        reminder_time=settings.club_night_reminder_time,
        timezone=settings.club_night_reminder_timezone,
    ):
        return SchedulerTickResult("skipped", "not_due", scheduled_post_id(club_night.event_date))

    store = MarkerStore(settings.club_night_reminder_state_path)
    claim = claim_scheduled_post_marker(
        store,
        guild_id,
        club_night.event_date,
        kind=CLUB_NIGHT_REMINDER_PREFIX,
        channel_id=channel_id,
        now=target_now,
        source_service="discord-bot",
        git_commit=_git_commit(),
    )
    if not claim.claimed:
        return SchedulerTickResult("skipped", claim.reason, claim.post_id)

    try:
        reminder = build_club_night_reminder_text(
            club_night,
            template_path=settings.club_night_reminder_template_path,
            today=local_now.date(),
        )
        channel = await _get_text_channel(bot_client, channel_id)
        message = await channel.send(reminder.text, allowed_mentions=NO_MENTIONS)
        message_id = int(getattr(message, "id", 0))
        mark_scheduled_post_posted(
            store,
            guild_id,
            claim.post_id,
            message_id=message_id,
            now=target_now,
        )
        return SchedulerTickResult("posted", "posted", claim.post_id, message_id)
    except (ReminderTemplateError, TypeError, discord.DiscordException) as exc:
        mark_scheduled_post_failed(
            store,
            guild_id,
            claim.post_id,
            error=exc,
            now=target_now,
        )
        return SchedulerTickResult("failed", exc.__class__.__name__, claim.post_id)


async def run_club_night_announcement_tick(
    bot_client: Any,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> SchedulerTickResult:
    guild_id = _resolve_guild_id(bot_client, settings)
    if guild_id is None:
        return SchedulerTickResult("skipped", "guild_unavailable")

    target_now = now or datetime.now(UTC)
    local_now = _local_datetime(target_now, settings.club_night_reminder_timezone)

    try:
        threads = await collect_forum_threads(
            bot_client,
            settings.club_night_forum_channel_id,
        )
    except ForumClubNightLookupError:
        return SchedulerTickResult("failed", "club_night_forum_lookup_failed")

    latest = find_latest_announcement_club_night(threads, today=local_now.date())

    if latest is not None and latest.event_date >= local_now.date():
        return SchedulerTickResult(
            "skipped",
            "announcement_exists",
            scheduled_post_id(latest.event_date, prefix=CLUB_NIGHT_ANNOUNCEMENT_PREFIX),
        )

    if not is_announcement_due(
        latest.event_date if latest is not None else None,
        target_now,
        post_time=settings.club_night_announcement_time,
        timezone=settings.club_night_reminder_timezone,
    ):
        return SchedulerTickResult("skipped", "not_due")

    try:
        club_night = await get_next_club_night(
            settings.battlefields_events_url,
            settings.cardiff_stadium_events_timeout_seconds,
            settings.cardiff_stadium_events_cache_seconds,
            settings.cardiff_stadium_events_max_bytes,
            today=local_now.date(),
        )
    except StadiumEventsError:
        return SchedulerTickResult("failed", "next_club_night_unavailable")

    store = MarkerStore(settings.club_night_reminder_state_path)
    claim = claim_scheduled_post_marker(
        store,
        guild_id,
        club_night.event_date,
        kind=CLUB_NIGHT_ANNOUNCEMENT_PREFIX,
        channel_id=settings.club_night_forum_channel_id,
        now=target_now,
        source_service="discord-bot",
        git_commit=_git_commit(),
    )
    if not claim.claimed:
        return SchedulerTickResult("skipped", claim.reason, claim.post_id)

    try:
        title = build_announcement_title(club_night.event_date)
        body = load_announcement_body(settings.club_night_announcement_template_path)
        body = append_stadium_status(
            body,
            await get_stadium_status_lines(settings, club_night.event_date),
        )
        forum_channel = await _get_forum_channel(
            bot_client,
            settings.club_night_forum_channel_id,
        )
        applied_tags = get_applied_tags(forum_channel, settings.club_night_forum_tag_id)
        reason = "Scheduled Blind Faith organised play announcement"

        if applied_tags is None:
            created = await forum_channel.create_thread(
                name=title,
                content=body,
                allowed_mentions=NO_MENTIONS,
                reason=reason,
            )
        else:
            created = await forum_channel.create_thread(
                name=title,
                content=body,
                allowed_mentions=NO_MENTIONS,
                applied_tags=applied_tags,
                reason=reason,
            )

        thread = getattr(created, "thread", None) or created
        thread_id = int(getattr(thread, "id", 0))
        mark_scheduled_post_posted(
            store,
            guild_id,
            claim.post_id,
            message_id=thread_id,
            now=target_now,
        )
        return SchedulerTickResult("posted", "posted", claim.post_id, thread_id)
    except (
        AnnouncementTemplateError,
        ClubNightAnnouncementError,
        TypeError,
        discord.DiscordException,
    ) as exc:
        mark_scheduled_post_failed(
            store,
            guild_id,
            claim.post_id,
            error=exc,
            now=target_now,
        )
        return SchedulerTickResult("failed", exc.__class__.__name__, claim.post_id)


def _local_datetime(now: datetime, timezone: str) -> datetime:
    target_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return target_now.astimezone(ZoneInfo(timezone))


def _resolve_guild_id(bot_client: Any, settings: Settings) -> str | None:
    guild_id = settings.discord_guild_id
    if guild_id is None:
        guilds = getattr(bot_client, "guilds", ())
        guild_id = guilds[0].id if guilds else None
    if guild_id is None:
        return None
    return validate_guild_id(str(guild_id))


async def _get_text_channel(bot_client: Any, channel_id: int) -> Any:
    channel = bot_client.get_channel(channel_id)
    if channel is None:
        channel = await bot_client.fetch_channel(channel_id)

    if not hasattr(channel, "send"):
        raise TypeError(f"Configured reminder channel {channel_id} cannot send messages.")

    return channel


async def _get_forum_channel(bot_client: Any, channel_id: int) -> Any:
    channel = bot_client.get_channel(channel_id)
    if channel is None:
        channel = await bot_client.fetch_channel(channel_id)

    if not hasattr(channel, "create_thread"):
        raise TypeError(f"Configured forum channel {channel_id} cannot create posts.")

    return channel


def _truncate_failure(value: str, max_chars: int = 500) -> str:
    cleaned = " ".join(value.split())
    return cleaned if len(cleaned) <= max_chars else cleaned[:max_chars]


def _git_commit() -> str:
    return os.getenv("GIT_COMMIT", "unknown")
