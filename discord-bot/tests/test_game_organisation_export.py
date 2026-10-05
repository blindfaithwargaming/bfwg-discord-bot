import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import discord

from bot.services.game_organisation_export import (
    collect_game_organisation_snapshot,
    is_export_due,
    run_game_organisation_export_tick,
    snapshot_generated_at,
    write_snapshot,
)
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT


class _AsyncItems:
    def __init__(self, items: list) -> None:
        self._items = iter(items)

    def __aiter__(self) -> "_AsyncItems":
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeThread:
    def __init__(
        self,
        thread_id: int,
        name: str,
        created_at: datetime | None,
        *,
        archived: bool = False,
        messages: list | None = None,
        history_error: Exception | None = None,
    ) -> None:
        self.id = thread_id
        self.name = name
        self.created_at = created_at
        self.archived = archived
        self.parent_id = 123
        self.history_calls: list[dict] = []
        self._messages = messages or []
        self._history_error = history_error

    def history(self, *, limit: int | None, oldest_first: bool) -> _AsyncItems:
        self.history_calls.append({"limit": limit, "oldest_first": oldest_first})
        if self._history_error is not None:
            raise self._history_error
        return _AsyncItems(self._messages)


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
    ) -> None:
        self.id = 123
        self.guild = FakeGuildThreads(active_threads or [])
        self._archived_threads = archived_threads or []
        self.archived_limits: list[int | None] = []

    def archived_threads(self, *, limit: int | None) -> _AsyncItems:
        self.archived_limits.append(limit)
        return _AsyncItems(self._archived_threads)


class FakeForumBot:
    def __init__(self, forum: FakeForum) -> None:
        self.guilds = [SimpleNamespace(id=123456789012345678)]
        self._forum = forum

    def get_channel(self, channel_id: int) -> FakeForum:
        return self._forum

    async def fetch_channel(self, channel_id: int) -> FakeForum:
        return self._forum


def _message(
    author_id: int,
    display_name: str,
    content: str,
    *,
    bot: bool = False,
    created_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        author=SimpleNamespace(id=author_id, display_name=display_name, bot=bot),
        created_at=created_at or datetime(2026, 7, 1, 18, 0, tzinfo=UTC),
        content=content,
    )


def _settings(export_path: Path, **overrides: object) -> SimpleNamespace:
    defaults = dict(
        game_organisation_channel_id=123,
        game_organisation_export_path=str(export_path),
        game_organisation_export_max_age_hours=24,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_snapshot_captures_threads_and_messages_in_created_order() -> None:
    newer = FakeThread(
        2,
        "Organised Play - 25th June",
        datetime(2026, 6, 12, 13, 0, tzinfo=UTC),
        messages=[
            _message(42, "Aaron", "I'll bring Iron Warriors"),
            _message(0, "BFWG Bot", "Next organised play!", bot=True),
        ],
    )
    older = FakeThread(
        1,
        "Organised Play - 11th June",
        datetime(2026, 5, 29, 13, 0, tzinfo=UTC),
        archived=True,
        messages=[_message(43, "Bex", "Sallies for me")],
    )
    bot = FakeForumBot(FakeForum(active_threads=[newer], archived_threads=[older]))

    snapshot = asyncio.run(
        collect_game_organisation_snapshot(
            bot,
            123,
            now=datetime(2026, 7, 7, 3, 0, tzinfo=UTC),
        )
    )

    assert snapshot["schema_version"] == 1
    assert snapshot["generated_at"] == "2026-07-07T03:00:00+00:00"
    assert snapshot["channel_id"] == 123
    assert snapshot["thread_count"] == 2
    assert [thread["id"] for thread in snapshot["threads"]] == [1, 2]

    archived_thread = snapshot["threads"][0]
    assert archived_thread["title"] == "Organised Play - 11th June"
    assert archived_thread["archived"] is True
    assert archived_thread["fetch_failed"] is False
    assert archived_thread["messages_truncated"] is False
    assert archived_thread["message_count"] == 1

    active_messages = snapshot["threads"][1]["messages"]
    assert active_messages[0] == {
        "author_id": 42,
        "author_display_name": "Aaron",
        "author_is_bot": False,
        "created_at": "2026-07-01T18:00:00+00:00",
        "content": "I'll bring Iron Warriors",
    }
    assert active_messages[1]["author_is_bot"] is True


def test_snapshot_requests_full_history_oldest_first() -> None:
    thread = FakeThread(1, "Thread", datetime(2026, 6, 1, tzinfo=UTC))
    forum = FakeForum(archived_threads=[thread])
    bot = FakeForumBot(forum)

    asyncio.run(collect_game_organisation_snapshot(bot, 123))

    assert forum.archived_limits == [None]
    assert thread.history_calls == [{"limit": None, "oldest_first": True}]


def test_snapshot_marks_threads_truncated_by_an_explicit_message_limit() -> None:
    thread = FakeThread(
        1,
        "Thread",
        datetime(2026, 6, 1, tzinfo=UTC),
        messages=[_message(42, "Aaron", "one"), _message(43, "Bex", "two")],
    )
    bot = FakeForumBot(FakeForum(active_threads=[thread]))

    snapshot = asyncio.run(
        collect_game_organisation_snapshot(bot, 123, message_limit=2)
    )

    assert snapshot["threads"][0]["messages_truncated"] is True
    assert snapshot["threads"][0]["message_count"] == 2


def test_snapshot_truncates_long_message_content() -> None:
    thread = FakeThread(
        1,
        "Thread",
        datetime(2026, 6, 1, tzinfo=UTC),
        messages=[_message(42, "Aaron", "x" * 5000)],
    )
    bot = FakeForumBot(FakeForum(active_threads=[thread]))

    snapshot = asyncio.run(collect_game_organisation_snapshot(bot, 123))

    content = snapshot["threads"][0]["messages"][0]["content"]
    assert len(content) == DISCORD_MESSAGE_LIMIT
    assert content.endswith("...")


def test_snapshot_flags_unreadable_thread_without_losing_the_rest() -> None:
    forbidden = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "denied"
    )
    unreadable = FakeThread(
        1,
        "Locked",
        datetime(2026, 6, 1, tzinfo=UTC),
        history_error=forbidden,
    )
    readable = FakeThread(
        2,
        "Open",
        datetime(2026, 6, 2, tzinfo=UTC),
        messages=[_message(42, "Aaron", "Knights this week")],
    )
    bot = FakeForumBot(FakeForum(active_threads=[unreadable, readable]))

    snapshot = asyncio.run(collect_game_organisation_snapshot(bot, 123))

    assert snapshot["thread_count"] == 2
    flagged = snapshot["threads"][0]
    assert flagged["fetch_failed"] is True
    assert flagged["messages"] == []
    assert snapshot["threads"][1]["message_count"] == 1


def test_snapshot_dedupes_threads_seen_as_active_and_archived() -> None:
    thread = FakeThread(1, "Thread", datetime(2026, 6, 1, tzinfo=UTC))
    bot = FakeForumBot(FakeForum(active_threads=[thread], archived_threads=[thread]))

    snapshot = asyncio.run(collect_game_organisation_snapshot(bot, 123))

    assert snapshot["thread_count"] == 1
    assert thread.history_calls == [{"limit": None, "oldest_first": True}]


def test_write_snapshot_round_trips_and_overwrites_corrupt_file(tmp_path: Path) -> None:
    target = tmp_path / "state" / "game_organisation_export.json"
    target.parent.mkdir()
    target.write_text("{not json", encoding="utf-8")

    snapshot = {"schema_version": 1, "generated_at": "2026-07-07T03:00:00+00:00"}
    write_snapshot(snapshot, target)

    assert json.loads(target.read_text(encoding="utf-8")) == snapshot
    assert list(target.parent.iterdir()) == [target]


def test_snapshot_generated_at_handles_missing_corrupt_and_naive(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    assert snapshot_generated_at(missing) is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert snapshot_generated_at(corrupt) is None

    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text(json.dumps(["nope"]), encoding="utf-8")
    assert snapshot_generated_at(wrong_shape) is None

    naive = tmp_path / "naive.json"
    naive.write_text(
        json.dumps({"generated_at": "2026-07-07T03:00:00"}), encoding="utf-8"
    )
    assert snapshot_generated_at(naive) == datetime(2026, 7, 7, 3, 0, tzinfo=UTC)


def test_is_export_due_for_missing_fresh_and_stale(tmp_path: Path) -> None:
    now = datetime(2026, 7, 7, 3, 0, tzinfo=UTC)
    path = tmp_path / "snapshot.json"

    assert is_export_due(path, now=now, max_age_hours=24) is True

    write_snapshot({"generated_at": (now - timedelta(hours=23)).isoformat()}, path)
    assert is_export_due(path, now=now, max_age_hours=24) is False

    write_snapshot({"generated_at": (now - timedelta(hours=24)).isoformat()}, path)
    assert is_export_due(path, now=now, max_age_hours=24) is True


def test_tick_skips_when_channel_unconfigured(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "snapshot.json", game_organisation_channel_id=None)

    result = asyncio.run(
        run_game_organisation_export_tick(FakeForumBot(FakeForum()), settings)
    )

    assert (result.status, result.reason) == ("skipped", "channel_unconfigured")


def test_tick_skips_when_snapshot_fresh(tmp_path: Path) -> None:
    now = datetime(2026, 7, 7, 3, 0, tzinfo=UTC)
    path = tmp_path / "snapshot.json"
    write_snapshot({"generated_at": (now - timedelta(hours=1)).isoformat()}, path)
    forum = FakeForum()

    result = asyncio.run(
        run_game_organisation_export_tick(
            FakeForumBot(forum), _settings(path), now=now
        )
    )

    assert (result.status, result.reason) == ("skipped", "snapshot_fresh")
    assert forum.archived_limits == []


def test_tick_exports_when_snapshot_missing(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    thread = FakeThread(
        1,
        "Organised Play - 25th June",
        datetime(2026, 6, 12, 13, 0, tzinfo=UTC),
        messages=[_message(42, "Aaron", "Iron Warriors again")],
    )
    bot = FakeForumBot(FakeForum(active_threads=[thread]))

    result = asyncio.run(
        run_game_organisation_export_tick(
            bot,
            _settings(path),
            now=datetime(2026, 7, 7, 3, 0, tzinfo=UTC),
        )
    )

    assert (result.status, result.reason) == ("exported", "threads=1")
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["generated_at"] == "2026-07-07T03:00:00+00:00"
    assert written["threads"][0]["messages"][0]["content"] == "Iron Warriors again"


def test_tick_fails_softly_when_forum_unreadable(tmp_path: Path) -> None:
    class NotAForum:
        pass

    class Bot:
        def get_channel(self, channel_id: int) -> NotAForum:
            return NotAForum()

        async def fetch_channel(self, channel_id: int) -> NotAForum:
            return NotAForum()

    path = tmp_path / "snapshot.json"

    result = asyncio.run(run_game_organisation_export_tick(Bot(), _settings(path)))

    assert (result.status, result.reason) == ("failed", "forum_lookup_failed")
    assert not path.exists()


def test_tick_fails_softly_when_snapshot_unwritable(tmp_path: Path) -> None:
    blocker = tmp_path / "state"
    blocker.write_text("a file where the directory should be", encoding="utf-8")
    path = blocker / "snapshot.json"
    bot = FakeForumBot(FakeForum(active_threads=[]))

    result = asyncio.run(run_game_organisation_export_tick(bot, _settings(path)))

    assert (result.status, result.reason) == ("failed", "snapshot_write_failed")
