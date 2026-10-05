import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime

from bot.services.club_night_forum import (
    RECENT_FORUM_THREAD_LIMIT,
    collect_forum_threads,
    find_latest_announcement_club_night,
    find_next_announcement_club_night,
    get_next_forum_club_night,
    parse_announcement_title_date,
    parse_thread_club_night,
)
from bot.services.stadium_events import ClubNight


@dataclass(frozen=True)
class FakeThread:
    name: str
    id: int = 555
    parent_id: int = 123
    created_at: datetime | None = None


class FakeGuild:
    def __init__(self, active_threads: list[FakeThread]) -> None:
        self._active_threads = active_threads

    async def active_threads(self) -> list[FakeThread]:
        return self._active_threads


class FakeArchivedThreads:
    def __init__(self, threads: list[FakeThread]) -> None:
        self._threads = iter(threads)

    def __aiter__(self) -> "FakeArchivedThreads":
        return self

    async def __anext__(self) -> FakeThread:
        try:
            return next(self._threads)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeForumChannel:
    def __init__(
        self,
        *,
        active_threads: list[FakeThread],
        archived_threads: list[FakeThread],
    ) -> None:
        self.id = 123
        self.guild = FakeGuild(active_threads)
        self._archived_threads = archived_threads
        self.archived_limit: int | None = None

    def archived_threads(self, *, limit: int) -> FakeArchivedThreads:
        self.archived_limit = limit
        return FakeArchivedThreads(self._archived_threads)


class FakeBot:
    def __init__(self, forum: FakeForumChannel | None) -> None:
        self._forum = forum

    def get_channel(self, channel_id: int) -> FakeForumChannel | None:
        return self._forum

    async def fetch_channel(self, channel_id: int) -> FakeForumChannel:
        if self._forum is None:
            raise RuntimeError("channel unavailable")
        return self._forum


def test_parse_announcement_title_date_accepts_generated_titles() -> None:
    assert parse_announcement_title_date(
        "Organised Play - 11th June",
        today=date(2026, 6, 1),
        created_at=datetime(2026, 5, 30, tzinfo=UTC),
    ) == date(2026, 6, 11)


def test_parse_announcement_title_date_handles_cross_year_posts() -> None:
    assert parse_announcement_title_date(
        "Organised Play - 5th January",
        today=date(2025, 12, 20),
        created_at=datetime(2025, 12, 15, tzinfo=UTC),
    ) == date(2026, 1, 5)


def test_parse_announcement_title_date_ignores_old_title_without_thread_date() -> None:
    assert (
        parse_announcement_title_date(
            "Organised Play - 11th May",
            today=date(2026, 6, 11),
        )
        is None
    )


def test_parse_announcement_title_date_does_not_roll_past_titles_into_next_year() -> None:
    assert (
        parse_announcement_title_date(
            "Organised Play - 30th October",
            today=date(2026, 6, 12),
            created_at=datetime(2025, 10, 20, tzinfo=UTC),
        )
        is None
    )


def test_parse_announcement_title_date_allow_past_returns_past_dates() -> None:
    assert parse_announcement_title_date(
        "Organised Play - 11th May",
        today=date(2026, 6, 11),
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
        allow_past=True,
    ) == date(2026, 5, 11)


def test_parse_announcement_title_date_allow_past_resolves_cross_year_posts() -> None:
    assert parse_announcement_title_date(
        "Organised Play - 28th December",
        today=date(2027, 1, 2),
        created_at=datetime(2026, 12, 20, tzinfo=UTC),
        allow_past=True,
    ) == date(2026, 12, 28)


def test_find_latest_announcement_club_night_returns_most_recent_past_or_future() -> None:
    threads = [
        FakeThread("General chat", created_at=datetime(2026, 6, 1, tzinfo=UTC)),
        FakeThread("Organised Play - 11th May", created_at=datetime(2026, 5, 1, tzinfo=UTC)),
        FakeThread("Organised Play - 11th June", created_at=datetime(2026, 6, 1, tzinfo=UTC)),
        FakeThread("Organised Play - 28th May", created_at=datetime(2026, 5, 18, tzinfo=UTC)),
    ]

    assert find_latest_announcement_club_night(
        threads,
        today=date(2026, 6, 12),
    ) == ClubNight("Organised Play - 11th June", date(2026, 6, 11))


def test_find_latest_announcement_club_night_returns_none_without_announcements() -> None:
    threads = [FakeThread("General chat", created_at=datetime(2026, 6, 1, tzinfo=UTC))]

    assert find_latest_announcement_club_night(threads, today=date(2026, 6, 12)) is None


def test_collect_forum_threads_filters_active_threads_to_forum_channel() -> None:
    matching = FakeThread("Organised Play - 21st June")
    archived = FakeThread(
        "Organised Play - 11th June",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    forum = FakeForumChannel(
        active_threads=[FakeThread("Other forum post", parent_id=999), matching],
        archived_threads=[archived],
    )

    async def _run() -> list:
        return await collect_forum_threads(FakeBot(forum), 123)

    assert asyncio.run(_run()) == [matching, archived]
    assert forum.archived_limit == RECENT_FORUM_THREAD_LIMIT


def test_find_next_announcement_club_night_uses_earliest_upcoming_thread() -> None:
    threads = [
        FakeThread("General chat", created_at=datetime(2026, 6, 1, tzinfo=UTC)),
        FakeThread("Organised Play - 11th May", created_at=datetime(2026, 5, 1, tzinfo=UTC)),
        FakeThread("Organised Play - 19th July", created_at=datetime(2026, 7, 1, tzinfo=UTC)),
        FakeThread("Organised Play - 21st June", created_at=datetime(2026, 6, 1, tzinfo=UTC)),
    ]

    assert find_next_announcement_club_night(
        threads,
        today=date(2026, 6, 11),
    ) == ClubNight("Organised Play - 21st June", date(2026, 6, 21))


def test_find_next_announcement_club_night_ignores_year_old_archived_posts() -> None:
    threads = [
        FakeThread("Organised Play - 30th October", created_at=datetime(2025, 10, 20, tzinfo=UTC)),
        FakeThread("Organised Play - 11th June", created_at=datetime(2026, 5, 28, tzinfo=UTC)),
    ]

    assert find_next_announcement_club_night(threads, today=date(2026, 6, 12)) is None


def test_get_next_forum_club_night_checks_active_and_recent_archived_threads() -> None:
    forum = FakeForumChannel(
        active_threads=[
            FakeThread("Organised Play - 21st June", parent_id=999),
            FakeThread("General chat"),
        ],
        archived_threads=[
            FakeThread(
                "Organised Play - 21st June",
                created_at=datetime(2026, 6, 1, tzinfo=UTC),
            ),
        ],
    )

    async def _run() -> ClubNight | None:
        return await get_next_forum_club_night(
            FakeBot(forum),
            123,
            today=date(2026, 6, 11),
        )

    assert asyncio.run(_run()) == ClubNight("Organised Play - 21st June", date(2026, 6, 21))
    assert forum.archived_limit == RECENT_FORUM_THREAD_LIMIT


def test_parse_thread_club_night_accepts_past_club_night_thread() -> None:
    thread = FakeThread(
        "Organised Play - 8th July",
        created_at=datetime(2026, 6, 25, tzinfo=UTC),
    )

    club_night = parse_thread_club_night(thread, 123, today=date(2026, 7, 9))

    assert club_night == ClubNight(
        "Organised Play - 8th July", date(2026, 7, 8), thread_id="555"
    )


def test_parse_thread_club_night_rejects_wrong_parent_channel() -> None:
    thread = FakeThread(
        "Organised Play - 8th July",
        parent_id=999,
        created_at=datetime(2026, 6, 25, tzinfo=UTC),
    )

    assert parse_thread_club_night(thread, 123, today=date(2026, 7, 9)) is None


def test_parse_thread_club_night_rejects_non_thread_channel() -> None:
    class FakeTextChannel:
        name = "general"

    assert parse_thread_club_night(FakeTextChannel(), 123, today=date(2026, 7, 9)) is None
    assert parse_thread_club_night(None, 123, today=date(2026, 7, 9)) is None


def test_parse_thread_club_night_rejects_unparseable_title() -> None:
    thread = FakeThread(
        "General chat",
        created_at=datetime(2026, 6, 25, tzinfo=UTC),
    )

    assert parse_thread_club_night(thread, 123, today=date(2026, 7, 9)) is None
