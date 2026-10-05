import logging
from datetime import date
from pathlib import Path
from typing import Any, Iterable, TypeVar

from bot.config import Settings
from bot.services.stadium_events import (
    ClubNight,
    StadiumEvent,
    StadiumEventsError,
    events_on_date,
    format_date,
    get_events,
    get_next_club_night,
)
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT


ThreadT = TypeVar("ThreadT")


class AnnouncementTemplateError(RuntimeError):
    """Raised when an announcement template cannot be loaded."""


class ClubNightAnnouncementError(RuntimeError):
    """Raised when the configured Discord forum cannot be used."""


def build_announcement_title(event_date: date) -> str:
    return f"Heresy Organised Play - {_ordinal(event_date.day)} {event_date.strftime('%B')}"


def is_duplicate_announcement_title(existing_title: str, new_title: str) -> bool:
    return _normalize_title(existing_title) == _normalize_title(new_title)


def find_duplicate_announcement_thread(threads: Iterable[ThreadT], title: str) -> ThreadT | None:
    for thread in threads:
        thread_title = getattr(thread, "name", "")

        if isinstance(thread_title, str) and is_duplicate_announcement_title(
            thread_title,
            title,
        ):
            return thread

    return None


def load_announcement_body(template_path: str) -> str:
    path = Path(template_path)

    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AnnouncementTemplateError(f"Could not read announcement template: {path}") from exc

    if not body:
        raise AnnouncementTemplateError(f"Announcement template is empty: {path}")

    if len(body) > DISCORD_MESSAGE_LIMIT:
        raise AnnouncementTemplateError(
            f"Announcement template is {len(body)} characters; Discord messages are limited to {DISCORD_MESSAGE_LIMIT}."
        )

    return body


def append_stadium_status(body: str, status_lines: Iterable[str]) -> str:
    status = "\n".join(status_lines).strip()
    updated_body = f"{body.rstrip()}\n\n{status}".strip()

    if len(updated_body) > DISCORD_MESSAGE_LIMIT:
        raise AnnouncementTemplateError(
            f"Announcement post is {len(updated_body)} characters after adding stadium status; Discord messages are limited to {DISCORD_MESSAGE_LIMIT}."
        )

    return updated_body


def build_stadium_status_lines(stadium_matches: list[StadiumEvent]) -> list[str]:
    matches = list(stadium_matches)

    if not matches:
        return ["Cardiff City Stadium check: no listed stadium event found for this evening."]

    event_lines = [f"- {event.title} at {event.starts_at.strftime('%H:%M')}" for event in matches]
    return [
        "Cardiff City Stadium check: listed stadium event(s) found for this evening:",
        *event_lines,
    ]


async def get_stadium_status_lines(
    settings: Settings,
    event_date: date,
    *,
    refresh: bool = False,
) -> list[str]:
    try:
        stadium_snapshot = await get_events(
            settings.cardiff_stadium_events_url,
            settings.cardiff_stadium_events_timeout_seconds,
            settings.cardiff_stadium_events_cache_seconds,
            settings.cardiff_stadium_events_max_bytes,
            force_refresh=refresh,
        )
    except StadiumEventsError as exc:
        logging.warning("Could not check Cardiff City Stadium events for announcement: %s", exc)
        return ["Cardiff City Stadium check: could not check listed stadium events for this evening."]

    return build_stadium_status_lines(events_on_date(stadium_snapshot.events, event_date))


async def build_announcement_content(
    settings: Settings,
    *,
    refresh: bool = False,
) -> tuple[ClubNight, str, str]:
    club_night = await get_next_club_night(
        settings.battlefields_events_url,
        settings.cardiff_stadium_events_timeout_seconds,
        settings.cardiff_stadium_events_cache_seconds,
        settings.cardiff_stadium_events_max_bytes,
        force_refresh=refresh,
    )
    title = build_announcement_title(club_night.event_date)
    body = load_announcement_body(settings.club_night_announcement_template_path)
    stadium_status_lines = await get_stadium_status_lines(
        settings,
        club_night.event_date,
        refresh=refresh,
    )
    body = append_stadium_status(body, stadium_status_lines)
    return club_night, title, body


def get_applied_tags(forum_channel: Any, tag_id: int | None) -> list[Any] | None:
    if tag_id is None:
        return None

    tag = forum_channel.get_tag(tag_id)

    if tag is None:
        raise ClubNightAnnouncementError(
            f"Configured forum tag {tag_id} does not exist in {forum_channel.mention}."
        )

    return [tag]


def _ordinal(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

    return f"{day}{suffix}"


def _normalize_title(value: str) -> str:
    normalized = " ".join(value.casefold().split())
    for prefix in ("horus heresy ", "heresy "):
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


class ReminderTemplateError(RuntimeError):
    """Raised when a reminder template cannot be loaded or rendered."""


def load_reminder_body(template_path: str) -> str:
    path = Path(template_path)

    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ReminderTemplateError(f"Could not read reminder template: {path}") from exc

    if not body:
        raise ReminderTemplateError(f"Reminder template is empty: {path}")

    if len(body) > DISCORD_MESSAGE_LIMIT:
        raise ReminderTemplateError(
            f"Reminder template is {len(body)} characters; Discord messages are limited to {DISCORD_MESSAGE_LIMIT}."
        )

    return body


def build_reminder_text(body: str, title: str, event_date: date, days_until: int) -> str:
    days = max(0, days_until)

    if days == 0:
        days_label = "today"
    elif days == 1:
        days_label = "tomorrow"
    else:
        days_label = f"in {days} days"

    try:
        return body.format(
            title=title,
            date=format_date(event_date),
            days=days,
            days_label=days_label,
        )
    except KeyError as exc:
        raise ReminderTemplateError(
            f"Reminder template contains unknown placeholder: {exc}"
        ) from exc
