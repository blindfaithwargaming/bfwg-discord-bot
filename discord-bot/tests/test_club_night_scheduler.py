import asyncio
import json
from datetime import UTC, date, datetime, time as dt_time
from pathlib import Path
from types import SimpleNamespace

from bot.services.club_night_scheduler import (
    CLUB_NIGHT_ANNOUNCEMENT_PREFIX,
    MarkerStore,
    claim_scheduled_post_marker,
    is_announcement_due,
    mark_scheduled_post_failed,
    mark_scheduled_post_posted,
    scheduled_post_id,
)
from bot.services.stadium_events import ClubNight, StadiumEventsError


GUILD_ID = "123456789012345678"


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, text: str, *, allowed_mentions: object) -> SimpleNamespace:
        self.sent.append({"text": text, "allowed_mentions": allowed_mentions})
        return SimpleNamespace(id=987654321)


class FakeBot:
    def __init__(self, channel: FakeChannel | None = None) -> None:
        self.guilds = [SimpleNamespace(id=123456789012345678)]
        self._channel = channel

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self._channel

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        if self._channel is None:
            raise RuntimeError("channel unavailable")
        return self._channel


def _settings(state_path: Path, **overrides: object) -> SimpleNamespace:
    defaults = dict(
        discord_guild_id=123456789012345678,
        club_night_forum_channel_id=123,
        battlefields_events_url="https://www.thebattlefields.co.uk/events",
        cardiff_stadium_events_timeout_seconds=5,
        cardiff_stadium_events_cache_seconds=900,
        cardiff_stadium_events_max_bytes=1_000_000,
        club_night_reminder_channel_id=111222333444555666,
        club_night_reminder_days_before=3,
        club_night_reminder_time=dt_time(19, 0),
        club_night_reminder_timezone="Europe/London",
        club_night_reminder_template_path="content/club_night_reminder.md",
        club_night_reminder_state_path=str(state_path),
        club_night_forum_tag_id=None,
        club_night_announcement_template_path="content/organised-play-announcement.md",
        club_night_announcement_time=dt_time(13, 0),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_is_reminder_due_before_uk_local_time() -> None:
    from bot.services.club_night_scheduler import is_reminder_due

    assert (
        is_reminder_due(
            date(2026, 6, 11),
            datetime(2026, 6, 8, 17, 59, tzinfo=UTC),
            days_before=3,
            reminder_time=dt_time(19, 0),
            timezone="Europe/London",
        )
        is False
    )


def test_is_reminder_due_at_uk_local_time() -> None:
    from bot.services.club_night_scheduler import is_reminder_due

    assert (
        is_reminder_due(
            date(2026, 6, 11),
            datetime(2026, 6, 8, 18, 0, tzinfo=UTC),
            days_before=3,
            reminder_time=dt_time(19, 0),
            timezone="Europe/London",
        )
        is True
    )


def test_is_reminder_due_does_not_catch_up_after_due_date() -> None:
    from bot.services.club_night_scheduler import is_reminder_due

    assert (
        is_reminder_due(
            date(2026, 6, 11),
            datetime(2026, 6, 9, 18, 0, tzinfo=UTC),
            days_before=3,
            reminder_time=dt_time(19, 0),
            timezone="Europe/London",
        )
        is False
    )


def test_is_reminder_due_allows_same_day_reminders() -> None:
    from bot.services.club_night_scheduler import is_reminder_due

    assert (
        is_reminder_due(
            date(2026, 6, 11),
            datetime(2026, 6, 11, 14, 0, tzinfo=UTC),
            days_before=0,
            reminder_time=dt_time(15, 0),
            timezone="Europe/London",
        )
        is True
    )


def test_claim_scheduled_post_marker_creates_marker(tmp_path: Path) -> None:
    store = MarkerStore(tmp_path / "markers.json")
    event_date = date(2026, 6, 11)

    claim = claim_scheduled_post_marker(
        store,
        GUILD_ID,
        event_date,
        channel_id=111,
        now=datetime(2026, 6, 8, 18, 0, tzinfo=UTC),
        source_service="discord-bot",
        git_commit="abc123",
    )

    post_id = scheduled_post_id(event_date)
    marker = store.get(GUILD_ID, post_id)

    assert claim.claimed is True
    assert claim.attempt_count == 1
    assert marker["status"] == "pending"
    assert marker["channel_id"] == "111"


def test_claim_scheduled_post_marker_skips_already_posted_marker(tmp_path: Path) -> None:
    store = MarkerStore(tmp_path / "markers.json")
    event_date = date(2026, 6, 11)
    post_id = scheduled_post_id(event_date)
    store.put(GUILD_ID, post_id, {"status": "posted", "attempt_count": 1})

    claim = claim_scheduled_post_marker(
        store,
        GUILD_ID,
        event_date,
        channel_id=111,
        now=datetime(2026, 6, 8, 18, 0, tzinfo=UTC),
        source_service="discord-bot",
        git_commit="abc123",
    )

    assert claim.claimed is False
    assert claim.reason == "already_posted"


def test_claim_scheduled_post_marker_retries_failed_marker_until_cap(tmp_path: Path) -> None:
    store = MarkerStore(tmp_path / "markers.json")
    event_date = date(2026, 6, 11)
    post_id = scheduled_post_id(event_date)
    store.put(GUILD_ID, post_id, {"status": "failed", "attempt_count": 2})

    claim = claim_scheduled_post_marker(
        store,
        GUILD_ID,
        event_date,
        channel_id=111,
        now=datetime(2026, 6, 8, 19, 0, tzinfo=UTC),
        source_service="discord-bot",
        git_commit="abc123",
    )

    assert claim.claimed is True
    assert claim.attempt_count == 3
    assert store.get(GUILD_ID, post_id)["status"] == "pending"


def test_claim_scheduled_post_marker_stops_after_attempt_cap(tmp_path: Path) -> None:
    store = MarkerStore(tmp_path / "markers.json")
    event_date = date(2026, 6, 11)
    post_id = scheduled_post_id(event_date)
    store.put(GUILD_ID, post_id, {"status": "failed", "attempt_count": 3})

    claim = claim_scheduled_post_marker(
        store,
        GUILD_ID,
        event_date,
        channel_id=111,
        now=datetime(2026, 6, 8, 19, 0, tzinfo=UTC),
        source_service="discord-bot",
        git_commit="abc123",
    )

    assert claim.claimed is False
    assert claim.reason == "attempts_exhausted"


def test_marker_store_persists_across_reload(tmp_path: Path) -> None:
    """A fresh MarkerStore on the same path sees previously written markers.

    This is what protects against duplicate reminders after a container restart.
    """
    path = tmp_path / "markers.json"
    event_date = date(2026, 6, 11)
    post_id = scheduled_post_id(event_date)

    mark_scheduled_post_posted(
        MarkerStore(path),
        GUILD_ID,
        post_id,
        message_id=987654321,
        now=datetime(2026, 6, 8, 18, 5, tzinfo=UTC),
    )

    reloaded = claim_scheduled_post_marker(
        MarkerStore(path),
        GUILD_ID,
        event_date,
        channel_id=111,
        now=datetime(2026, 6, 8, 18, 6, tzinfo=UTC),
        source_service="discord-bot",
        git_commit="abc123",
    )

    assert reloaded.claimed is False
    assert reloaded.reason == "already_posted"


def test_marker_store_ignores_non_dict_guild_entries(tmp_path: Path) -> None:
    path = tmp_path / "markers.json"
    path.write_text(
        json.dumps({GUILD_ID: "bogus", "other": ["also bogus"]}),
        encoding="utf-8",
    )
    store = MarkerStore(path)

    assert store.get(GUILD_ID, "club-night-reminder-2026-06-11") is None

    store.put(GUILD_ID, "club-night-reminder-2026-06-11", {"status": "pending"})

    assert store.get(GUILD_ID, "club-night-reminder-2026-06-11") == {"status": "pending"}


def test_mark_scheduled_post_failed_records_reason(tmp_path: Path) -> None:
    store = MarkerStore(tmp_path / "markers.json")
    event_date = date(2026, 6, 11)
    post_id = scheduled_post_id(event_date)
    store.put(GUILD_ID, post_id, {"status": "pending", "attempt_count": 1})

    mark_scheduled_post_failed(
        store,
        GUILD_ID,
        post_id,
        error=RuntimeError("send failed"),
        now=datetime(2026, 6, 8, 19, 0, tzinfo=UTC),
    )

    marker = store.get(GUILD_ID, post_id)
    assert marker["status"] == "failed"
    assert marker["failure_reason"] == "send failed"


def test_run_tick_skips_when_channel_unconfigured(tmp_path: Path) -> None:
    from bot.services.club_night_scheduler import run_club_night_reminder_tick

    channel = FakeChannel()

    async def _run():
        return await run_club_night_reminder_tick(
            FakeBot(channel),
            _settings(tmp_path / "markers.json", club_night_reminder_channel_id=None),
            now=datetime(2026, 6, 8, 18, 0, tzinfo=UTC),
        )

    result = asyncio.run(_run())

    assert result.status == "skipped"
    assert result.reason == "channel_unconfigured"
    assert channel.sent == []


def test_run_tick_posts_due_reminder_and_marks_posted(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    template = tmp_path / "reminder.md"
    template.write_text("Reminder: {title} is {days_label}", encoding="utf-8")
    channel = FakeChannel()
    state_path = tmp_path / "markers.json"
    club_night = ClubNight("Organised Play - 11th June", date(2026, 6, 11))

    async def fake_get_next_forum_club_night(*_args, **_kwargs):
        return club_night

    monkeypatch.setattr(
        scheduler,
        "get_next_forum_club_night",
        fake_get_next_forum_club_night,
    )

    async def _run():
        return await scheduler.run_club_night_reminder_tick(
            FakeBot(channel),
            _settings(state_path, club_night_reminder_template_path=str(template)),
            now=datetime(2026, 6, 8, 18, 0, tzinfo=UTC),
        )

    result = asyncio.run(_run())
    marker = MarkerStore(state_path).get(GUILD_ID, "club-night-reminder-2026-06-11")

    assert result.status == "posted"
    assert channel.sent[0]["text"] == "Reminder: Organised Play - 11th June is in 3 days"
    assert marker["status"] == "posted"
    assert marker["message_id"] == "987654321"


def test_run_tick_skips_when_no_forum_post(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    async def fake_get_next_forum_club_night(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        scheduler,
        "get_next_forum_club_night",
        fake_get_next_forum_club_night,
    )

    async def _run():
        return await scheduler.run_club_night_reminder_tick(
            FakeBot(FakeChannel()),
            _settings(tmp_path / "markers.json"),
            now=datetime(2026, 6, 8, 18, 0, tzinfo=UTC),
        )

    result = asyncio.run(_run())

    assert result.status == "skipped"
    assert result.reason == "club_night_forum_post_not_found"


def test_run_tick_does_not_repost_after_restart(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    template = tmp_path / "reminder.md"
    template.write_text("Reminder: {title} is {days_label}", encoding="utf-8")
    state_path = tmp_path / "markers.json"
    club_night = ClubNight("Organised Play - 11th June", date(2026, 6, 11))

    async def fake_get_next_forum_club_night(*_args, **_kwargs):
        return club_night

    monkeypatch.setattr(
        scheduler,
        "get_next_forum_club_night",
        fake_get_next_forum_club_night,
    )

    async def _tick(channel: FakeChannel):
        return await scheduler.run_club_night_reminder_tick(
            FakeBot(channel),
            _settings(state_path, club_night_reminder_template_path=str(template)),
            now=datetime(2026, 6, 8, 18, 0, tzinfo=UTC),
        )

    first_channel = FakeChannel()
    first = asyncio.run(_tick(first_channel))

    # Simulate a restart: a brand-new tick reading the same persisted state file.
    second_channel = FakeChannel()
    second = asyncio.run(_tick(second_channel))

    assert first.status == "posted"
    assert second.status == "skipped"
    assert second.reason == "already_posted"
    assert second_channel.sent == []

def test_is_announcement_due_without_previous_uses_post_time() -> None:
    assert (
        is_announcement_due(
            None,
            datetime(2026, 6, 12, 11, 30, tzinfo=UTC),
            post_time=dt_time(13, 0),
            timezone="Europe/London",
        )
        is False
    )
    assert (
        is_announcement_due(
            None,
            datetime(2026, 6, 12, 12, 30, tzinfo=UTC),
            post_time=dt_time(13, 0),
            timezone="Europe/London",
        )
        is True
    )


def test_is_announcement_due_waits_for_previous_club_night_to_pass() -> None:
    assert (
        is_announcement_due(
            date(2026, 6, 12),
            datetime(2026, 6, 12, 14, 0, tzinfo=UTC),
            post_time=dt_time(13, 0),
            timezone="Europe/London",
        )
        is False
    )


def test_is_announcement_due_day_after_previous_at_post_time() -> None:
    assert (
        is_announcement_due(
            date(2026, 6, 11),
            datetime(2026, 6, 12, 11, 0, tzinfo=UTC),
            post_time=dt_time(13, 0),
            timezone="Europe/London",
        )
        is False
    )
    assert (
        is_announcement_due(
            date(2026, 6, 11),
            datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
            post_time=dt_time(13, 0),
            timezone="Europe/London",
        )
        is True
    )


def test_is_announcement_due_catches_up_on_later_days() -> None:
    assert (
        is_announcement_due(
            date(2026, 6, 11),
            datetime(2026, 6, 14, 8, 0, tzinfo=UTC),
            post_time=dt_time(13, 0),
            timezone="Europe/London",
        )
        is True
    )


class _AsyncThreads:
    def __init__(self, threads: list) -> None:
        self._threads = iter(threads)

    def __aiter__(self) -> "_AsyncThreads":
        return self

    async def __anext__(self):
        try:
            return next(self._threads)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeGuildThreads:
    def __init__(self, threads: list) -> None:
        self._threads = threads

    async def active_threads(self) -> list:
        return self._threads


class FakeForum:
    def __init__(
        self,
        active_threads: list | None = None,
        archived_threads: list | None = None,
        tag: object | None = None,
    ) -> None:
        self.id = 123
        self.mention = "#club-night"
        self.guild = FakeGuildThreads(active_threads or [])
        self._archived_threads = archived_threads or []
        self._tag = tag
        self.created: list[dict] = []

    def archived_threads(self, *, limit: int) -> _AsyncThreads:
        return _AsyncThreads(self._archived_threads)

    def get_tag(self, tag_id: int) -> object | None:
        return self._tag

    async def create_thread(self, **kwargs: object) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(thread=SimpleNamespace(id=555666777))


class FakeForumBot:
    def __init__(self, forum: FakeForum) -> None:
        self.guilds = [SimpleNamespace(id=123456789012345678)]
        self._forum = forum

    def get_channel(self, channel_id: int) -> FakeForum:
        return self._forum

    async def fetch_channel(self, channel_id: int) -> FakeForum:
        return self._forum


def _forum_thread(name: str, created: datetime) -> SimpleNamespace:
    return SimpleNamespace(name=name, parent_id=123, created_at=created)


def _patch_announcement_sources(monkeypatch, club_night: ClubNight | None) -> None:
    import bot.services.club_night_scheduler as scheduler

    async def fake_get_next_club_night(*_args: object, **_kwargs: object) -> ClubNight:
        if club_night is None:
            raise StadiumEventsError("No upcoming Blind Faith club night could be found.")
        return club_night

    async def fake_get_stadium_status_lines(
        *_args: object, **_kwargs: object
    ) -> list[str]:
        return ["Cardiff City Stadium check: no listed stadium event found for this evening."]

    monkeypatch.setattr(scheduler, "get_next_club_night", fake_get_next_club_night)
    monkeypatch.setattr(scheduler, "get_stadium_status_lines", fake_get_stadium_status_lines)


def _announcement_settings(tmp_path: Path, **overrides: object) -> SimpleNamespace:
    template = tmp_path / "announcement.md"
    if not template.exists():
        template.write_text("Bring a 3,000 point army.", encoding="utf-8")
    return _settings(
        tmp_path / "markers.json",
        club_night_announcement_template_path=str(template),
        **overrides,
    )


def test_announcement_tick_posts_day_after_previous_club_night(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    forum = FakeForum(
        archived_threads=[
            _forum_thread("Organised Play - 11th June", datetime(2026, 6, 1, tzinfo=UTC)),
        ],
    )
    _patch_announcement_sources(
        monkeypatch,
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 6, 25)),
    )

    async def _run():
        return await scheduler.run_club_night_announcement_tick(
            FakeForumBot(forum),
            _announcement_settings(tmp_path),
            now=datetime(2026, 6, 12, 12, 30, tzinfo=UTC),
        )

    result = asyncio.run(_run())
    marker = MarkerStore(tmp_path / "markers.json").get(
        GUILD_ID, "club-night-announcement-2026-06-25"
    )

    assert result.status == "posted"
    assert result.post_id == "club-night-announcement-2026-06-25"
    assert forum.created[0]["name"] == "Heresy Organised Play - 25th June"
    assert "Bring a 3,000 point army." in forum.created[0]["content"]
    assert "Cardiff City Stadium check" in forum.created[0]["content"]
    assert "applied_tags" not in forum.created[0]
    assert marker["status"] == "posted"
    assert marker["kind"] == CLUB_NIGHT_ANNOUNCEMENT_PREFIX
    assert marker["message_id"] == "555666777"


def test_announcement_tick_skips_when_announcement_already_exists(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    forum = FakeForum(
        active_threads=[
            _forum_thread("Organised Play - 25th June", datetime(2026, 6, 12, tzinfo=UTC)),
        ],
    )
    _patch_announcement_sources(
        monkeypatch,
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 6, 25)),
    )

    async def _run():
        return await scheduler.run_club_night_announcement_tick(
            FakeForumBot(forum),
            _announcement_settings(tmp_path),
            now=datetime(2026, 6, 13, 12, 30, tzinfo=UTC),
        )

    result = asyncio.run(_run())

    assert result.status == "skipped"
    assert result.reason == "announcement_exists"
    assert forum.created == []


def test_announcement_tick_not_due_before_post_time(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    forum = FakeForum(
        archived_threads=[
            _forum_thread("Organised Play - 11th June", datetime(2026, 6, 1, tzinfo=UTC)),
        ],
    )
    _patch_announcement_sources(
        monkeypatch,
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 6, 25)),
    )

    async def _run():
        return await scheduler.run_club_night_announcement_tick(
            FakeForumBot(forum),
            _announcement_settings(tmp_path),
            now=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
        )

    result = asyncio.run(_run())

    assert result.status == "skipped"
    assert result.reason == "not_due"
    assert forum.created == []


def test_announcement_tick_does_not_repost_after_restart(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    _patch_announcement_sources(
        monkeypatch,
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 6, 25)),
    )

    def _forum() -> FakeForum:
        return FakeForum(
            archived_threads=[
                _forum_thread("Organised Play - 11th June", datetime(2026, 6, 1, tzinfo=UTC)),
            ],
        )

    async def _tick(forum: FakeForum):
        return await scheduler.run_club_night_announcement_tick(
            FakeForumBot(forum),
            _announcement_settings(tmp_path),
            now=datetime(2026, 6, 12, 12, 30, tzinfo=UTC),
        )

    first_forum = _forum()
    first = asyncio.run(_tick(first_forum))

    second_forum = _forum()
    second = asyncio.run(_tick(second_forum))

    assert first.status == "posted"
    assert second.status == "skipped"
    assert second.reason == "already_posted"
    assert second_forum.created == []


def test_announcement_tick_fails_when_next_club_night_unavailable(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    forum = FakeForum(
        archived_threads=[
            _forum_thread("Organised Play - 11th June", datetime(2026, 6, 1, tzinfo=UTC)),
        ],
    )
    _patch_announcement_sources(monkeypatch, None)

    async def _run():
        return await scheduler.run_club_night_announcement_tick(
            FakeForumBot(forum),
            _announcement_settings(tmp_path),
            now=datetime(2026, 6, 12, 12, 30, tzinfo=UTC),
        )

    result = asyncio.run(_run())

    assert result.status == "failed"
    assert result.reason == "next_club_night_unavailable"
    assert forum.created == []
    assert MarkerStore(tmp_path / "markers.json").get(
        GUILD_ID, "club-night-announcement-2026-06-25"
    ) is None


def test_announcement_tick_posts_on_first_enable_without_history(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    forum = FakeForum()
    _patch_announcement_sources(
        monkeypatch,
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 6, 25)),
    )

    async def _run():
        return await scheduler.run_club_night_announcement_tick(
            FakeForumBot(forum),
            _announcement_settings(tmp_path),
            now=datetime(2026, 6, 12, 12, 30, tzinfo=UTC),
        )

    result = asyncio.run(_run())

    assert result.status == "posted"
    assert forum.created[0]["name"] == "Heresy Organised Play - 25th June"


def test_announcement_tick_applies_configured_forum_tag(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    tag = SimpleNamespace(id=42)
    forum = FakeForum(tag=tag)
    _patch_announcement_sources(
        monkeypatch,
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 6, 25)),
    )

    async def _run():
        return await scheduler.run_club_night_announcement_tick(
            FakeForumBot(forum),
            _announcement_settings(tmp_path, club_night_forum_tag_id=42),
            now=datetime(2026, 6, 12, 12, 30, tzinfo=UTC),
        )

    result = asyncio.run(_run())

    assert result.status == "posted"
    assert forum.created[0]["applied_tags"] == [tag]


def test_announcement_tick_marks_failed_when_tag_missing(monkeypatch, tmp_path: Path) -> None:
    import bot.services.club_night_scheduler as scheduler

    forum = FakeForum(tag=None)
    _patch_announcement_sources(
        monkeypatch,
        ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 6, 25)),
    )

    async def _run():
        return await scheduler.run_club_night_announcement_tick(
            FakeForumBot(forum),
            _announcement_settings(tmp_path, club_night_forum_tag_id=42),
            now=datetime(2026, 6, 12, 12, 30, tzinfo=UTC),
        )

    result = asyncio.run(_run())
    marker = MarkerStore(tmp_path / "markers.json").get(
        GUILD_ID, "club-night-announcement-2026-06-25"
    )

    assert result.status == "failed"
    assert result.reason == "ClubNightAnnouncementError"
    assert forum.created == []
    assert marker["status"] == "failed"
    assert "forum tag 42" in marker["failure_reason"]
