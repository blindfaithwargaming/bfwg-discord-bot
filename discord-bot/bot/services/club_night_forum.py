from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable

import discord

from bot.services.stadium_events import ClubNight
from bot.services.organised_play import normalize_game

RECENT_FORUM_THREAD_LIMIT = 50
ANNOUNCEMENT_TITLE_RE = re.compile(
    r"^\s*(?:(?P<game>.+?)\s+)?organised\s+play\s*-\s*"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?P<month>[A-Za-z]+)\s*$",
    re.IGNORECASE,
)


class ForumClubNightLookupError(RuntimeError):
    """Raised when the configured Discord forum cannot be read."""


def parse_announcement_title_date(
    title: str,
    *,
    today: date,
    created_at: datetime | None = None,
    allow_past: bool = False,
) -> date | None:
    match = ANNOUNCEMENT_TITLE_RE.match(title)
    if match is None:
        return None

    try:
        day = int(match.group("day"))
        month = _parse_month(match.group("month"))
    except ValueError:
        return None

    created_date = created_at.date() if created_at is not None else None
    years = (created_date.year, created_date.year + 1) if created_date else (today.year,)

    for year in years:
        try:
            event_date = date(year, month, day)
        except ValueError:
            continue

        if created_date is not None and event_date < created_date:
            continue
        if allow_past or event_date >= today:
            return event_date
        # This is the date the title meant when the thread was created — the
        # year + 1 fallback exists only for December posts announcing January
        # events. A past event must not roll forward into next year, or old
        # archived posts become phantom future club nights.
        return None

    return None


def find_next_announcement_club_night(
    threads: Iterable[Any],
    *,
    today: date,
) -> ClubNight | None:
    club_nights: list[ClubNight] = []

    for thread in threads:
        title = getattr(thread, "name", "")
        if not isinstance(title, str):
            continue

        event_date = parse_announcement_title_date(
            title,
            today=today,
            created_at=_thread_created_at(thread),
        )
        if event_date is not None and announcement_game(title) == "heresy":
            club_nights.append(ClubNight(title=title, event_date=event_date))

    return min(club_nights, key=lambda club_night: club_night.event_date, default=None)


def find_latest_announcement_club_night(
    threads: Iterable[Any],
    *,
    today: date,
) -> ClubNight | None:
    """Most recent announced club night, past or future.

    Used by the announcement scheduler to derive when the previous club night
    happened, so the next announcement can go out the day after it.
    """
    club_nights: list[ClubNight] = []

    for thread in threads:
        title = getattr(thread, "name", "")
        if not isinstance(title, str):
            continue

        event_date = parse_announcement_title_date(
            title,
            today=today,
            created_at=_thread_created_at(thread),
            allow_past=True,
        )
        if event_date is not None and announcement_game(title) == "heresy":
            club_nights.append(ClubNight(title=title, event_date=event_date))

    return max(club_nights, key=lambda club_night: club_night.event_date, default=None)


def parse_thread_club_night(
    channel: Any,
    forum_channel_id: int,
    *,
    today: date,
) -> ClubNight | None:
    """Club night a thread belongs to, or ``None`` if it is not one.

    Past dates are accepted — games and other follow-ups land in a club-night
    thread after the night has happened.
    """
    if getattr(channel, "parent_id", None) != forum_channel_id:
        return None

    title = getattr(channel, "name", "")
    if not isinstance(title, str):
        return None

    event_date = parse_announcement_title_date(
        title,
        today=today,
        created_at=_thread_created_at(channel),
        allow_past=True,
    )
    if event_date is None:
        return None

    return ClubNight(
        title=title,
        event_date=event_date,
        game=announcement_game(title),
        thread_id=str(channel.id),
    )


async def collect_forum_threads(
    bot_client: Any,
    forum_channel_id: int,
    *,
    archived_limit: int | None = RECENT_FORUM_THREAD_LIMIT,
) -> list[Any]:
    """Active plus archived threads for a forum channel.

    ``archived_limit=None`` paginates the channel's full archived history.
    """
    forum_channel = await _get_forum_channel(bot_client, forum_channel_id)
    threads: list[Any] = []

    try:
        active_threads = await forum_channel.guild.active_threads()
        threads.extend(
            thread
            for thread in active_threads
            if getattr(thread, "parent_id", None) == forum_channel.id
        )

        async for thread in forum_channel.archived_threads(limit=archived_limit):
            threads.append(thread)
    except discord.Forbidden as exc:
        raise ForumClubNightLookupError(
            f"Could not read recent posts in configured forum channel {forum_channel_id}."
        ) from exc
    except discord.HTTPException as exc:
        raise ForumClubNightLookupError(
            f"Could not read recent posts in configured forum channel {forum_channel_id}: {exc}"
        ) from exc

    return threads


async def get_next_forum_club_night(
    bot_client: Any,
    forum_channel_id: int,
    *,
    today: date | None = None,
    archived_limit: int = RECENT_FORUM_THREAD_LIMIT,
) -> ClubNight | None:
    target_today = today or date.today()
    threads = await collect_forum_threads(
        bot_client,
        forum_channel_id,
        archived_limit=archived_limit,
    )

    return find_next_announcement_club_night(threads, today=target_today)


async def _get_forum_channel(bot_client: Any, forum_channel_id: int) -> Any:
    channel = bot_client.get_channel(forum_channel_id)
    if channel is None:
        channel = await bot_client.fetch_channel(forum_channel_id)

    if not hasattr(channel, "archived_threads") or not hasattr(channel, "guild"):
        raise ForumClubNightLookupError(
            f"Configured channel {forum_channel_id} is not a readable forum channel."
        )

    return channel


def _parse_month(value: str) -> int:
    normalized = value.casefold()
    months = {
        "january": 1,
        "jan": 1,
        "february": 2,
        "feb": 2,
        "march": 3,
        "mar": 3,
        "april": 4,
        "apr": 4,
        "may": 5,
        "june": 6,
        "jun": 6,
        "july": 7,
        "jul": 7,
        "august": 8,
        "aug": 8,
        "september": 9,
        "sept": 9,
        "sep": 9,
        "october": 10,
        "oct": 10,
        "november": 11,
        "nov": 11,
        "december": 12,
        "dec": 12,
    }
    try:
        return months[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown month: {value}") from exc


def _thread_created_at(thread: Any) -> datetime | None:
    created_at = getattr(thread, "created_at", None)
    return created_at if isinstance(created_at, datetime) else None


def announcement_game(title: str) -> str:
    match = ANNOUNCEMENT_TITLE_RE.match(title)
    game = (
        " ".join((match.group("game") or "heresy").casefold().split())
        if match
        else "heresy"
    )
    return normalize_game(game)
