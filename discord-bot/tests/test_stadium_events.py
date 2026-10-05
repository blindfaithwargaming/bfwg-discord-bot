import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from bot.services import stadium_events
from bot.services.stadium_events import (
    ClubNight,
    StadiumEvent,
    StadiumEventSnapshot,
    StadiumEventsCache,
    StadiumEventsError,
    cache_age_seconds,
    events_on_date,
    format_date,
    get_events,
    get_next_club_night,
    parse_club_nights_from_html,
    parse_date,
    parse_events_from_html,
)

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("2026-07-04", date(2026, 7, 4)),
        ("04/07/2026", date(2026, 7, 4)),
        ("4 July 2026", date(2026, 7, 4)),
        ("July 4 2026", date(2026, 7, 4)),
        ("today", date(2026, 5, 11)),
        ("tomorrow", date(2026, 5, 12)),
    ),
)
def test_parse_date_accepts_supported_formats(value: str, expected: date) -> None:
    assert parse_date(value, today=date(2026, 5, 11)) == expected


def test_parse_date_rejects_unsupported_format() -> None:
    with pytest.raises(ValueError, match="Date must be like"):
        parse_date("next Friday")


def test_parse_events_from_html_extracts_and_sorts_events() -> None:
    html = """
    <html>
      <body>
        <div>Loading, please wait. Wales v England 2026-07-04 17:30 expand_less</div>
        <div>Summer Concert 2026-07-05 20:00</div>
      </body>
    </html>
    """

    events = parse_events_from_html(html)

    assert events == [
        StadiumEvent("Wales v England", datetime(2026, 7, 4, 17, 30)),
        StadiumEvent("Summer Concert", datetime(2026, 7, 5, 20, 0)),
    ]


def test_parse_events_from_html_deduplicates_same_start_time() -> None:
    html = """
    <div>Loading, please wait. Wales v England 2026-07-04 17:30 expand_less</div>
    <div>Wales v England 2026-07-04 17:30</div>
    """

    assert parse_events_from_html(html) == [
        StadiumEvent("Wales v England", datetime(2026, 7, 4, 17, 30))
    ]


def test_parse_club_nights_from_html_returns_future_blind_faith_events() -> None:
    html = """
    <div>Monday 11th May 2026 Blind Faith Organised Play - Cardiff Online: tickets</div>
    <div>Wednesday 13th May 2026 Other Event Online: tickets</div>
    <div>Monday 18th May 2026 Blind Faith Organised Play - Cardiff In-Store: tickets</div>
    """

    assert parse_club_nights_from_html(html, today=date(2026, 5, 12)) == [
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 5, 18))
    ]


def test_events_on_date_filters_by_calendar_date() -> None:
    events = [
        StadiumEvent("One", datetime(2026, 7, 4, 12, 0)),
        StadiumEvent("Two", datetime(2026, 7, 5, 12, 0)),
    ]

    assert events_on_date(events, date(2026, 7, 4)) == [events[0]]


def test_format_date_removes_leading_zero_from_day() -> None:
    assert format_date(date(2026, 7, 4)) == "Saturday 4 July 2026"


def test_get_next_club_night_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    html = """
    <div>Monday 18th May 2026 Blind Faith Organised Play - Cardiff Online: tickets</div>
    """

    async def fake_fetch_text(url: str, *_args: object) -> str:
        calls.append(url)
        return html

    async def run() -> None:
        monkeypatch.setattr(stadium_events, "fetch_text", fake_fetch_text)
        cache = StadiumEventsCache()

        first = await get_next_club_night(
            "https://example.test/events",
            5,
            900,
            1_000_000,
            today=date(2026, 5, 11),
            cache=cache,
        )
        second = await get_next_club_night(
            "https://example.test/events",
            5,
            900,
            1_000_000,
            today=date(2026, 5, 11),
            cache=cache,
        )

        assert first == second == ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 5, 18))

    asyncio.run(run())

    assert calls == ["https://example.test/events"]


def test_parse_events_from_html_returns_empty_for_no_events() -> None:
    html = "<html><body><p>No events scheduled.</p></body></html>"

    assert parse_events_from_html(html) == []


def test_get_events_returns_cached_snapshot_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_fetch_events(*_args: object, **_kwargs: object) -> list[StadiumEvent]:
        calls.append("fetched")
        return [StadiumEvent("Test Event", datetime(2026, 7, 4, 17, 30))]

    async def run() -> tuple[StadiumEventSnapshot, StadiumEventSnapshot]:
        monkeypatch.setattr(stadium_events, "fetch_events", fake_fetch_events)
        cache = StadiumEventsCache()
        first = await get_events("https://example.test/events", 5, 900, 1_000_000, cache=cache)
        second = await get_events("https://example.test/events", 5, 900, 1_000_000, cache=cache)
        return first, second

    first, second = asyncio.run(run())

    assert len(calls) == 1
    assert second.from_cache is True
    assert second.events == first.events


def test_get_events_raises_without_cache_when_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_events(*_args: object, **_kwargs: object) -> list[StadiumEvent]:
        raise StadiumEventsError("upstream failed")

    async def run() -> None:
        monkeypatch.setattr(stadium_events, "fetch_events", fake_fetch_events)
        await get_events("https://example.test/events", 5, 900, 1_000_000, cache=StadiumEventsCache())

    with pytest.raises(StadiumEventsError, match="upstream failed"):
        asyncio.run(run())


def test_get_next_club_night_raises_when_no_events_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_text(*_args: object, **_kwargs: object) -> str:
        return "<html><body>No events here.</body></html>"

    async def run() -> None:
        monkeypatch.setattr(stadium_events, "fetch_text", fake_fetch_text)
        await get_next_club_night(
            "https://example.test/events",
            5,
            900,
            1_000_000,
            today=date(2026, 5, 11),
            cache=StadiumEventsCache(),
        )

    with pytest.raises(StadiumEventsError, match="No upcoming Blind Faith"):
        asyncio.run(run())


def test_get_events_falls_back_to_cached_snapshot_on_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached_event = StadiumEvent("Cached Event", datetime(2026, 7, 4, 17, 30))
    cache = StadiumEventsCache(
        snapshot=StadiumEventSnapshot(events=[cached_event], fetched_at=datetime.now(UTC))
    )

    async def fake_fetch_events(*_args: object, **_kwargs: object) -> list[StadiumEvent]:
        raise StadiumEventsError("upstream failed")

    async def run() -> StadiumEventSnapshot:
        monkeypatch.setattr(stadium_events, "fetch_events", fake_fetch_events)
        return await get_events("https://example.test/events", 5, 900, 1_000_000, force_refresh=True, cache=cache)

    snapshot = asyncio.run(run())

    assert snapshot.events == [cached_event]
    assert snapshot.from_cache is True


# --- HTML fixture tests ---
# These tests load representative HTML saved from each external site and assert
# that the parsers extract the expected events.  Failures here signal markup
# drift on the live pages and are a prompt to update the fixture and parser.


def test_parse_events_from_html_fixture_cardiff_stadium() -> None:
    html = (_FIXTURES / "cardiff_stadium_events.html").read_text(encoding="utf-8")

    events = parse_events_from_html(html)

    assert events == [
        StadiumEvent("Wales v Scotland", datetime(2026, 9, 5, 17, 30)),
        StadiumEvent("Cardiff City FC v Swansea City AFC", datetime(2026, 10, 17, 15, 0)),
        StadiumEvent("Autumn Nations Series - Wales v New Zealand", datetime(2026, 11, 14, 17, 30)),
    ]


def test_parse_club_nights_from_html_fixture_battlefields_returns_blind_faith_events() -> None:
    html = (_FIXTURES / "battlefields_events.html").read_text(encoding="utf-8")

    club_nights = parse_club_nights_from_html(html, today=date(2026, 1, 1))

    assert club_nights == [
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 3, 9)),
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 4, 13)),
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 5, 11)),
    ]


def test_parse_club_nights_from_html_fixture_battlefields_excludes_non_blind_faith() -> None:
    html = (_FIXTURES / "battlefields_events.html").read_text(encoding="utf-8")

    club_nights = parse_club_nights_from_html(html, today=date(2026, 1, 1))
    titles = {cn.title for cn in club_nights}

    assert "Age of Sigmar Grand Tournament" not in titles
    assert "Horus Heresy Campaign Weekend" not in titles


def test_parse_club_nights_from_html_fixture_battlefields_filters_past_events() -> None:
    html = (_FIXTURES / "battlefields_events.html").read_text(encoding="utf-8")

    club_nights = parse_club_nights_from_html(html, today=date(2026, 4, 10))

    assert club_nights == [
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 4, 13)),
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 5, 11)),
    ]


def test_parse_club_nights_accepts_abbreviated_battlefields_months() -> None:
    html = """
    <p class="date">Thursday 11th Jun 2026</p>
    <h3 class="title match-height">
      The Horus Heresy: Blind Faith Wargaming Thursday Organised Play Cardiff
    </h3>
    <p>Online: £6.00</p>
    """

    club_nights = parse_club_nights_from_html(html, today=date(2026, 5, 30))

    assert club_nights == [
        ClubNight(
            "The Horus Heresy: Blind Faith Wargaming Thursday Organised Play Cardiff",
            date(2026, 6, 11),
        )
    ]


_CLUB_NIGHT_HTML_TEMPLATE = """
<p class="date">{date_str}</p>
<h3 class="title match-height">
  The Horus Heresy: Blind Faith Wargaming Thursday Organised Play Cardiff
</h3>
<p>Online: £6.00</p>
"""

_EXPECTED_CLUB_NIGHT = ClubNight(
    "The Horus Heresy: Blind Faith Wargaming Thursday Organised Play Cardiff",
    date(2026, 6, 11),
)


@pytest.mark.parametrize(
    "date_str",
    (
        pytest.param("Thursday 11th June 2026", id="full-day-full-month-ordinal"),
        pytest.param("Thursday 11th Jun 2026", id="full-day-short-month-ordinal"),
        pytest.param("Thursday 11 Jun 2026", id="full-day-short-month"),
        pytest.param("Thursday 11 June 2026", id="full-day-full-month"),
        pytest.param("Thu 11th Jun 2026", id="short-day-short-month-ordinal"),
        pytest.param("Thu 11th June 2026", id="short-day-full-month-ordinal"),
        pytest.param("Thu 11 Jun 2026", id="short-day-short-month"),
        pytest.param("Thu, 11th Jun. 2026", id="short-day-comma-dotted-month"),
        pytest.param("11th Jun 2026", id="no-day-short-month-ordinal"),
        pytest.param("11th June 2026", id="no-day-full-month-ordinal"),
        pytest.param("11 Jun 2026", id="no-day-short-month"),
        pytest.param("11 Jun, 2026", id="no-day-short-month-comma"),
        pytest.param("Thursday 11th JUN 2026", id="uppercase-month"),
        pytest.param("thursday 11th jun 2026", id="lowercase-input"),
    ),
)
def test_parse_club_nights_handles_date_format_variants(date_str: str) -> None:
    html = _CLUB_NIGHT_HTML_TEMPLATE.format(date_str=date_str)

    club_nights = parse_club_nights_from_html(html, today=date(2026, 5, 30))

    assert club_nights == [_EXPECTED_CLUB_NIGHT]


def test_parse_club_nights_skips_malformed_battlefields_dates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    html = """
    <p class="date">Thursday 31st Jun 2026</p>
    <h3 class="title match-height">
      The Horus Heresy: Blind Faith Wargaming Thursday Organised Play Cardiff
    </h3>
    <p>Online: £6.00</p>
    <p class="date">Thursday 11th Jun 2026</p>
    <h3 class="title match-height">
      The Horus Heresy: Blind Faith Wargaming Thursday Organised Play Cardiff
    </h3>
    <p>Online: £6.00</p>
    """

    with caplog.at_level(logging.WARNING):
        club_nights = parse_club_nights_from_html(html, today=date(2026, 5, 30))

    assert club_nights == [_EXPECTED_CLUB_NIGHT]
    assert "unrecognised date format" in caplog.text
    assert "possible markup change" in caplog.text


# --- StadiumEventsError reason field ---


def test_stadium_events_error_has_reason_field() -> None:
    err = StadiumEventsError("something broke", reason="parse_failure")
    assert err.reason == "parse_failure"
    assert str(err) == "something broke"


def test_stadium_events_error_default_reason_is_unknown() -> None:
    err = StadiumEventsError("oops")
    assert err.reason == "unknown"


def test_fetch_events_sets_parse_failure_reason_for_non_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_text(*_args: object, **_kwargs: object) -> str:
        return "<html><body><p>Events coming soon, check back later!</p></body></html>"

    async def run() -> None:
        monkeypatch.setattr(stadium_events, "fetch_text", fake_fetch_text)
        await stadium_events.fetch_events("https://example.test/events")

    with pytest.raises(StadiumEventsError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.reason == "parse_failure"


def test_fetch_events_sets_empty_response_reason_for_blank_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_text(*_args: object, **_kwargs: object) -> str:
        return "   "

    async def run() -> None:
        monkeypatch.setattr(stadium_events, "fetch_text", fake_fetch_text)
        await stadium_events.fetch_events("https://example.test/events")

    with pytest.raises(StadiumEventsError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.reason == "empty_response"


def test_fetch_events_logs_warning_for_parse_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    async def fake_fetch_text(*_args: object, **_kwargs: object) -> str:
        return "<html><body><p>No events, but markup is present.</p></body></html>"

    async def run() -> None:
        monkeypatch.setattr(stadium_events, "fetch_text", fake_fetch_text)
        await stadium_events.fetch_events("https://example.test/events")

    with caplog.at_level(logging.WARNING):
        with pytest.raises(StadiumEventsError):
            asyncio.run(run())

    assert "possible markup change" in caplog.text


# --- cache_age_seconds ---


def test_cache_age_seconds_returns_none_when_cache_empty() -> None:
    assert cache_age_seconds() is None


def test_cache_age_seconds_returns_float_after_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_events(*_args: object, **_kwargs: object) -> list[StadiumEvent]:
        return [StadiumEvent("Test Event", datetime(2026, 7, 4, 17, 30))]

    async def run() -> None:
        monkeypatch.setattr(stadium_events, "fetch_events", fake_fetch_events)
        await get_events("https://example.test/events", 5, 900, 1_000_000, cache=stadium_events._cache)

    stadium_events._cache.snapshot = None
    asyncio.run(run())
    age = cache_age_seconds()
    assert age is not None
    assert 0.0 <= age < 5.0
    stadium_events._cache.snapshot = None
