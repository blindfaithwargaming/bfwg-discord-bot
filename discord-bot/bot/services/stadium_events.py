import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from html import unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import ClientResponse


EVENT_PATTERN = re.compile(
    r"(?P<title>[A-Za-z0-9][A-Za-z0-9 '&().,/:+-]{1,120}?)\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<time>\d{2}:\d{2})\b"
)

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d %Y",
    "%b %d %Y",
)

BATTLEFIELDS_DAY_PATTERN = (
    r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"Mon|Tue|Wed|Thu|Fri|Sat|Sun"
)

BATTLEFIELDS_MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Sept|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
)

BATTLEFIELDS_EVENT_PATTERN = re.compile(
    r"(?<!\S)"
    r"(?P<date>"
    rf"(?:(?:{BATTLEFIELDS_DAY_PATTERN})\.?,?\s+)?"
    r"\d{1,2}(?:st|nd|rd|th)?\s+"
    rf"(?:{BATTLEFIELDS_MONTH_PATTERN})\.?,?\s+"
    r"\d{4}"
    r")\s+"
    r"(?P<title>.*?)(?=\s+(?:Online:|In-Store:|£|FREE)(?:\s|$))",
    re.IGNORECASE,
)

BATTLEFIELDS_DATE_FORMATS = (
    "%A %d %B %Y",  # Thursday 11 June 2026
    "%A %d %b %Y",  # Thursday 11 Jun 2026
    "%a %d %B %Y",  # Thu 11 June 2026
    "%a %d %b %Y",  # Thu 11 Jun 2026
    "%d %B %Y",  # 11 June 2026
    "%d %b %Y",  # 11 Jun 2026
)


class StadiumEventsError(RuntimeError):
    """Raised when stadium event data cannot be fetched or parsed.

    reason values: "parse_failure" (non-empty response, zero events parsed),
    "empty_response" (empty body), "fetch_error" (HTTP/network failure).
    """

    def __init__(self, message: str, *, reason: str = "unknown") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class StadiumEvent:
    title: str
    starts_at: datetime


@dataclass(frozen=True)
class ClubNight:
    title: str
    event_date: date
    game: str = "heresy"
    thread_id: str | None = None


@dataclass(frozen=True)
class StadiumEventSnapshot:
    events: list[StadiumEvent]
    fetched_at: datetime
    from_cache: bool = False


@dataclass
class StadiumEventsCache:
    snapshot: StadiumEventSnapshot | None = None
    club_night: ClubNight | None = None
    club_night_at: datetime | None = None


_cache = StadiumEventsCache()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(unescape(data).split())

        if text:
            self.chunks.append(text)


def parse_date(value: str, today: date | None = None) -> date:
    normalized = " ".join(value.strip().split())
    lower_normalized = normalized.lower()

    if lower_normalized == "today":
        return today or date.today()

    if lower_normalized == "tomorrow":
        return (today or date.today()) + timedelta(days=1)

    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(normalized, date_format).date()
        except ValueError:
            continue

    raise ValueError("Date must be like 2026-07-04, 04/07/2026, or 4 July 2026.")


def parse_events_from_html(html: str) -> list[StadiumEvent]:
    parser = _TextExtractor()
    parser.feed(html)

    events_by_key: dict[tuple[date, time], StadiumEvent] = {}

    for start_index in range(len(parser.chunks)):
        for window_size in range(1, 5):
            candidate = " ".join(parser.chunks[start_index : start_index + window_size])
            match = EVENT_PATTERN.search(candidate)

            if not match:
                continue

            title = _clean_title(match.group("title"))
            starts_at = datetime.strptime(
                f"{match.group('date')} {match.group('time')}",
                "%Y-%m-%d %H:%M",
            )

            if not title:
                continue

            key = (starts_at.date(), starts_at.time())
            event = StadiumEvent(title=title, starts_at=starts_at)
            existing = events_by_key.get(key)

            if existing is None or len(event.title) < len(existing.title):
                events_by_key[key] = event

    return sorted(events_by_key.values(), key=lambda event: event.starts_at)


def parse_club_nights_from_html(html: str, today: date | None = None) -> list[ClubNight]:
    target_today = today or date.today()
    parser = _TextExtractor()
    parser.feed(html)
    text = " ".join(parser.chunks)
    club_nights: list[ClubNight] = []

    for match in BATTLEFIELDS_EVENT_PATTERN.finditer(text):
        title = " ".join(match.group("title").split())

        if not _is_blind_faith_club_night(title):
            continue

        try:
            event_date = _parse_battlefields_date(match.group("date"))
        except ValueError:
            logging.warning(
                "club-night: unrecognised date format from Battlefields page; "
                "possible markup change date=%r title=%r",
                match.group("date"),
                title,
            )
            continue

        if event_date < target_today:
            continue

        club_nights.append(ClubNight(title=title, event_date=event_date))

    return sorted(club_nights, key=lambda club_night: club_night.event_date)


async def get_next_club_night(
    url: str,
    timeout_seconds: int,
    cache_seconds: int,
    max_bytes: int,
    today: date | None = None,
    force_refresh: bool = False,
    cache: StadiumEventsCache | None = None,
) -> ClubNight:
    if cache is None:
        cache = _cache

    now = datetime.now(UTC)
    target_today = today or date.today()

    if (
        not force_refresh
        and cache.club_night is not None
        and cache.club_night_at is not None
        and cache.club_night.event_date >= target_today
        and now - cache.club_night_at < timedelta(seconds=cache_seconds)
    ):
        return cache.club_night

    html = await fetch_text(url, timeout_seconds, max_bytes)
    club_nights = await asyncio.to_thread(parse_club_nights_from_html, html, target_today)

    if not club_nights:
        raise StadiumEventsError("No upcoming Blind Faith club night could be found.")

    cache.club_night = club_nights[0]
    cache.club_night_at = now
    return cache.club_night


async def get_events(
    url: str,
    timeout_seconds: int,
    cache_seconds: int,
    max_bytes: int,
    force_refresh: bool = False,
    cache: StadiumEventsCache | None = None,
) -> StadiumEventSnapshot:
    if cache is None:
        cache = _cache

    now = datetime.now(UTC)

    if (
        not force_refresh
        and cache.snapshot is not None
        and now - cache.snapshot.fetched_at < timedelta(seconds=cache_seconds)
    ):
        return StadiumEventSnapshot(
            events=cache.snapshot.events,
            fetched_at=cache.snapshot.fetched_at,
            from_cache=True,
        )

    try:
        events = await fetch_events(url, timeout_seconds, max_bytes)
    except StadiumEventsError as exc:
        if cache.snapshot is not None:
            logging.warning(
                "Using cached Cardiff City Stadium events after live fetch failed: %s",
                exc,
            )
            return StadiumEventSnapshot(
                events=cache.snapshot.events,
                fetched_at=cache.snapshot.fetched_at,
                from_cache=True,
            )

        raise

    cache.snapshot = StadiumEventSnapshot(events=events, fetched_at=now)
    return cache.snapshot


async def fetch_events(
    url: str,
    timeout_seconds: int = 5,
    max_bytes: int = 1_000_000,
) -> list[StadiumEvent]:
    html = await fetch_text(url, timeout_seconds, max_bytes)
    events = await asyncio.to_thread(parse_events_from_html, html)

    if not events:
        if html.strip():
            logging.warning(
                "stadium-events: non-empty response but zero events parsed; "
                "possible markup change url=%s body_bytes=%d",
                url,
                len(html.encode()),
            )
            raise StadiumEventsError(
                "No events could be parsed from the Cardiff City Stadium events page.",
                reason="parse_failure",
            )
        raise StadiumEventsError(
            "No events could be parsed from the Cardiff City Stadium events page.",
            reason="empty_response",
        )

    return events


async def fetch_text(
    url: str,
    timeout_seconds: int = 5,
    max_bytes: int = 1_000_000,
) -> str:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=timeout_seconds)
    headers = {
        "User-Agent": "bfwg-discord-bot/0.1 (+https://github.com/)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as response:
                logging.info(
                    "Fetched events page: url=%s status=%s content_type=%s",
                    url,
                    response.status,
                    response.headers.get("content-type", "unknown"),
                )
                response.raise_for_status()
                return await _read_limited_text(response, max_bytes)
    except TimeoutError as exc:
        raise StadiumEventsError(
            f"The events page did not respond within {timeout_seconds}s.",
            reason="fetch_error",
        ) from exc
    except aiohttp.ClientError as exc:
        raise StadiumEventsError(
            f"Could not fetch events page: {exc}",
            reason="fetch_error",
        ) from exc


async def _read_limited_text(response: "ClientResponse", max_bytes: int) -> str:
    body = await response.content.read(max_bytes + 1)

    if len(body) > max_bytes:
        raise StadiumEventsError(
            f"The Cardiff City Stadium events page exceeded the {max_bytes} byte response limit."
        )

    charset = response.charset or "utf-8"
    return body.decode(charset, errors="replace")


def cache_age_seconds() -> float | None:
    """Return seconds since the global event cache was last populated, or None if empty."""
    if _cache.snapshot is None:
        return None
    return (datetime.now(UTC) - _cache.snapshot.fetched_at).total_seconds()


def events_on_date(events: list[StadiumEvent], target_date: date) -> list[StadiumEvent]:
    return [event for event in events if event.starts_at.date() == target_date]


def format_date(value: date) -> str:
    return value.strftime("%A %d %B %Y").replace(" 0", " ")


def _clean_title(value: str) -> str:
    title = value.strip()
    title = re.sub(r"^(show_more\s+)?Loading,?\s+please wait\.?\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+(expand_less|close)$", "", title, flags=re.IGNORECASE)
    return title.strip(" -")


def _is_blind_faith_club_night(title: str) -> bool:
    normalized = title.lower()
    return (
        "blind faith" in normalized
        and "organised play" in normalized
        and "cardiff" in normalized
    )


def _parse_battlefields_date(value: str) -> date:
    cleaned = re.sub(r"[,\.]", " ", value)
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bSept\b", "Sep", cleaned, flags=re.IGNORECASE)
    # Title-case so strptime %a/%A/%b/%B work reliably across platforms/locales.
    cleaned = " ".join(cleaned.split()).title()

    for date_format in BATTLEFIELDS_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue

    raise ValueError(f"Unsupported Battlefields event date: {value}")
