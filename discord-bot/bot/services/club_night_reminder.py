from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from bot.services.club_night_announcement import build_reminder_text, load_reminder_body
from bot.services.stadium_events import ClubNight, get_next_club_night


@dataclass(frozen=True)
class ClubNightReminderContent:
    club_night: ClubNight
    days_until: int
    text: str


def build_club_night_reminder_text(
    club_night: ClubNight,
    *,
    template_path: str,
    today: date | None = None,
) -> ClubNightReminderContent:
    target_today = today or date.today()
    days_until = (club_night.event_date - target_today).days
    body = load_reminder_body(template_path)
    text = build_reminder_text(
        body,
        title=club_night.title,
        event_date=club_night.event_date,
        days_until=days_until,
    )
    return ClubNightReminderContent(
        club_night=club_night,
        days_until=days_until,
        text=text,
    )


async def build_club_night_reminder_content(
    *,
    battlefields_events_url: str,
    timeout_seconds: int,
    cache_seconds: int,
    max_bytes: int,
    template_path: str,
    today: date | None = None,
    force_refresh: bool = False,
) -> ClubNightReminderContent:
    target_today = today or date.today()
    club_night = await get_next_club_night(
        battlefields_events_url,
        timeout_seconds,
        cache_seconds,
        max_bytes,
        today=target_today,
        force_refresh=force_refresh,
    )
    return build_club_night_reminder_text(
        club_night,
        template_path=template_path,
        today=target_today,
    )
