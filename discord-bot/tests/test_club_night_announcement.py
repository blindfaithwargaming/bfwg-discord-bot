import asyncio
import importlib
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import pytest

from bot.services.club_night_announcement import (
    AnnouncementTemplateError,
    append_stadium_status,
    build_announcement_title,
    find_duplicate_announcement_thread,
    is_duplicate_announcement_title,
    load_announcement_body,
)
from bot.services.stadium_events import ClubNight, StadiumEventsError


@dataclass(frozen=True)
class FakeThread:
    name: str
    parent_id: int = 123


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
        active_threads: list[FakeThread],
        archived_threads: list[FakeThread],
    ) -> None:
        self.id = 123
        self.mention = "#club-night"
        self.guild = FakeGuild(active_threads)
        self._archived_threads = archived_threads
        self.archived_limit: int | None = None

    def archived_threads(self, *, limit: int) -> FakeArchivedThreads:
        self.archived_limit = limit
        return FakeArchivedThreads(self._archived_threads)


class FakeInteraction:
    def __init__(self, *, administrator: bool = False) -> None:
        self.guild_id = 123
        self.channel_id = 456
        self.user = SimpleNamespace(
            id=789,
            guild_permissions=SimpleNamespace(administrator=administrator),
        )
        self.client = SimpleNamespace()
        self.deferred = False
        self.followups: list[tuple[str, bool]] = []
        self.response_messages: list[tuple[str, bool]] = []

        async def _defer(*, ephemeral: bool = True, thinking: bool = True) -> None:
            self.deferred = True

        async def _send_message(message: str, *, ephemeral: bool = False) -> None:
            self.response_messages.append((message, ephemeral))

        async def _followup_send(message: str, *, ephemeral: bool = False) -> None:
            self.followups.append((message, ephemeral))

        self.response = SimpleNamespace(
            is_done=lambda: False,
            defer=_defer,
            send_message=_send_message,
        )
        self.followup = SimpleNamespace(send=_followup_send)


@pytest.mark.parametrize(
    ("event_date", "expected"),
    (
        (date(2026, 6, 1), "Heresy Organised Play - 1st June"),
        (date(2026, 6, 2), "Heresy Organised Play - 2nd June"),
        (date(2026, 6, 3), "Heresy Organised Play - 3rd June"),
        (date(2026, 6, 4), "Heresy Organised Play - 4th June"),
        (date(2026, 6, 11), "Heresy Organised Play - 11th June"),
        (date(2026, 6, 21), "Heresy Organised Play - 21st June"),
    ),
)
def test_build_announcement_title_uses_ordinal_day(event_date: date, expected: str) -> None:
    assert build_announcement_title(event_date) == expected


def test_is_duplicate_announcement_title_normalizes_case_and_spacing() -> None:
    assert is_duplicate_announcement_title(
        " organised   play - 21st june ",
        "Organised Play - 21st June",
    )


def test_find_duplicate_announcement_thread_returns_matching_thread() -> None:
    matching_thread = FakeThread("Organised Play - 21st June")
    threads = [
        FakeThread("General Chat"),
        matching_thread,
        FakeThread("Organised Play - 28th June"),
    ]

    assert (
        find_duplicate_announcement_thread(threads, "organised play - 21st june")
        is matching_thread
    )


def test_find_duplicate_announcement_thread_returns_none_without_match() -> None:
    threads = [FakeThread("Organised Play - 28th June")]

    assert find_duplicate_announcement_thread(threads, "Organised Play - 21st June") is None


def test_find_existing_forum_post_checks_active_forum_threads(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    command = importlib.import_module("bot.commands.club_night_announcement")
    matching_thread = FakeThread("Organised Play - 21st June")
    channel = FakeForumChannel(
        active_threads=[
            FakeThread("Organised Play - 21st June", parent_id=999),
            matching_thread,
        ],
        archived_threads=[],
    )

    assert (
        asyncio.run(command._find_existing_forum_post(channel, "organised play - 21st june"))
        is matching_thread
    )


def test_find_existing_forum_post_checks_recent_archived_threads(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    command = importlib.import_module("bot.commands.club_night_announcement")
    matching_thread = FakeThread("Organised Play - 21st June")
    channel = FakeForumChannel(
        active_threads=[FakeThread("Organised Play - 28th June")],
        archived_threads=[matching_thread],
    )

    assert (
        asyncio.run(command._find_existing_forum_post(channel, "Organised Play - 21st June"))
        is matching_thread
    )
    assert channel.archived_limit == command.RECENT_FORUM_THREAD_LIMIT


def test_club_night_next_rejects_non_admin_before_lookup(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    command = importlib.import_module("bot.commands.club_night_announcement")

    interaction = FakeInteraction(administrator=False)

    asyncio.run(command.club_night_next.callback(interaction, False))

    assert len(interaction.response_messages) == 1
    assert "administrator" in interaction.response_messages[0][0].lower()
    assert interaction.response_messages[0][1] is True
    assert interaction.deferred is False


def test_club_night_preview_rejects_non_admin_before_build(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    command = importlib.import_module("bot.commands.club_night_announcement")

    interaction = FakeInteraction(administrator=False)

    asyncio.run(command.club_night_preview.callback(interaction, False))

    assert len(interaction.response_messages) == 1
    assert "administrator" in interaction.response_messages[0][0].lower()
    assert interaction.response_messages[0][1] is True
    assert interaction.deferred is False


def test_load_announcement_body_trims_template(tmp_path) -> None:
    template = tmp_path / "announcement.md"
    template.write_text("\nBring a 3,000 point army.\n\n", encoding="utf-8")

    assert load_announcement_body(str(template)) == "Bring a 3,000 point army."


def test_load_announcement_body_rejects_empty_template(tmp_path) -> None:
    template = tmp_path / "announcement.md"
    template.write_text(" \n", encoding="utf-8")

    with pytest.raises(AnnouncementTemplateError, match="empty"):
        load_announcement_body(str(template))


def test_load_announcement_body_rejects_oversized_template(tmp_path) -> None:
    template = tmp_path / "announcement.md"
    template.write_text("x" * 2001, encoding="utf-8")

    with pytest.raises(AnnouncementTemplateError, match="Discord messages are limited"):
        load_announcement_body(str(template))


def test_append_stadium_status_adds_status_after_body() -> None:
    body = "Organised play details."
    status_lines = [
        "Cardiff City Stadium check: listed stadium event(s) found for this evening:",
        "- Wales v England at 17:30",
    ]

    assert append_stadium_status(body, status_lines) == (
        "Organised play details.\n\n"
        "Cardiff City Stadium check: listed stadium event(s) found for this evening:\n"
        "- Wales v England at 17:30"
    )


def test_append_stadium_status_rejects_oversized_post() -> None:
    with pytest.raises(AnnouncementTemplateError, match="after adding stadium status"):
        append_stadium_status("x" * 1999, ["too much"])


# --- _format_next_club_night_reply ---


def test__format_next_club_night_reply_includes_title_date_and_status(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    command = importlib.import_module("bot.commands.club_night_announcement")
    club_night = ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 5, 18))
    status_lines = ["Cardiff City Stadium check: no listed stadium event found for this evening."]

    reply = command._format_next_club_night_reply(club_night, status_lines)

    assert "Blind Faith Organised Play - Cardiff" in reply
    assert "Monday 18 May 2026" in reply
    assert "no listed stadium event" in reply
    assert "/club-night preview" in reply
    assert "/club-night signup-info" in reply


def test__format_next_club_night_reply_includes_stadium_events(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    command = importlib.import_module("bot.commands.club_night_announcement")
    club_night = ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 5, 18))
    status_lines = [
        "Cardiff City Stadium check: listed stadium event(s) found for this evening:",
        "- Wales v England at 17:30",
    ]

    reply = command._format_next_club_night_reply(club_night, status_lines)

    assert "Wales v England at 17:30" in reply
    assert "/club-night preview" in reply
    assert "/club-night signup-info" in reply


def test__format_next_club_night_reply_joins_multiline_status(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    command = importlib.import_module("bot.commands.club_night_announcement")
    club_night = ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 5, 18))
    status_lines = ["Line one.", "Line two."]

    reply = command._format_next_club_night_reply(club_night, status_lines)

    assert "Line one.\nLine two." in reply


# --- build_announcement_content ---


def _content_settings() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        battlefields_events_url="https://www.thebattlefields.co.uk/events",
        cardiff_stadium_events_timeout_seconds=5,
        cardiff_stadium_events_cache_seconds=900,
        cardiff_stadium_events_max_bytes=1_000_000,
        club_night_announcement_template_path="content/organised-play-announcement.md",
    )


def test_build_announcement_content_returns_club_night_title_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("bot.services.club_night_announcement")

    expected_club_night = ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 5, 18))

    async def fake_get_next_club_night(*args: object, **kwargs: object) -> ClubNight:
        return expected_club_night

    async def fake_get_stadium_status_lines(
        settings: object, event_date: object, *, refresh: bool = False
    ) -> list[str]:
        return ["Cardiff City Stadium check: no listed stadium event found for this evening."]

    monkeypatch.setattr(service, "get_next_club_night", fake_get_next_club_night)
    monkeypatch.setattr(service, "get_stadium_status_lines", fake_get_stadium_status_lines)
    monkeypatch.setattr(service, "load_announcement_body", lambda _: "Bring a 3,000 point army.")

    club_night, title, body = asyncio.run(
        service.build_announcement_content(_content_settings(), refresh=False)
    )

    assert club_night == expected_club_night
    assert title == "Heresy Organised Play - 18th May"
    assert "Bring a 3,000 point army." in body
    assert "Cardiff City Stadium check" in body


def test_build_announcement_content_propagates_stadium_events_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("bot.services.club_night_announcement")

    async def fake_get_next_club_night(*args: object, **kwargs: object) -> ClubNight:
        raise StadiumEventsError("No upcoming club night.")

    monkeypatch.setattr(service, "get_next_club_night", fake_get_next_club_night)

    with pytest.raises(StadiumEventsError, match="No upcoming club night"):
        asyncio.run(service.build_announcement_content(_content_settings(), refresh=False))


def test_build_announcement_content_propagates_template_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("bot.services.club_night_announcement")

    expected_club_night = ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 5, 18))

    async def fake_get_next_club_night(*args: object, **kwargs: object) -> ClubNight:
        return expected_club_night

    def fake_load_announcement_body(_: object) -> str:
        raise AnnouncementTemplateError("Could not read announcement template: missing.md")

    monkeypatch.setattr(service, "get_next_club_night", fake_get_next_club_night)
    monkeypatch.setattr(service, "load_announcement_body", fake_load_announcement_body)

    with pytest.raises(AnnouncementTemplateError, match="Could not read"):
        asyncio.run(service.build_announcement_content(_content_settings(), refresh=False))


def test_build_announcement_content_passes_refresh_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = importlib.import_module("bot.services.club_night_announcement")

    refresh_flags: list[bool] = []
    expected_club_night = ClubNight("Blind Faith Organised Play - Cardiff", date(2026, 5, 18))

    async def fake_get_next_club_night(
        *args: object, force_refresh: bool = False, **kwargs: object
    ) -> ClubNight:
        refresh_flags.append(force_refresh)
        return expected_club_night

    async def fake_get_stadium_status_lines(
        settings: object, event_date: object, *, refresh: bool = False
    ) -> list[str]:
        refresh_flags.append(refresh)
        return ["Cardiff City Stadium check: no listed stadium event found for this evening."]

    monkeypatch.setattr(service, "get_next_club_night", fake_get_next_club_night)
    monkeypatch.setattr(service, "get_stadium_status_lines", fake_get_stadium_status_lines)
    monkeypatch.setattr(service, "load_announcement_body", lambda _: "Bring a 3,000 point army.")

    asyncio.run(service.build_announcement_content(_content_settings(), refresh=True))

    assert all(flag is True for flag in refresh_flags)


# --- get_applied_tags ---


def test_get_applied_tags_returns_none_without_configured_tag() -> None:
    from bot.services.club_night_announcement import get_applied_tags

    assert get_applied_tags(object(), None) is None


def test_get_applied_tags_returns_configured_tag() -> None:
    from types import SimpleNamespace

    from bot.services.club_night_announcement import get_applied_tags

    tag = SimpleNamespace(id=42)
    forum_channel = SimpleNamespace(get_tag=lambda _tag_id: tag, mention="#club-night")

    assert get_applied_tags(forum_channel, 42) == [tag]


def test_get_applied_tags_rejects_missing_tag() -> None:
    from types import SimpleNamespace

    from bot.services.club_night_announcement import (
        ClubNightAnnouncementError,
        get_applied_tags,
    )

    forum_channel = SimpleNamespace(get_tag=lambda _tag_id: None, mention="#club-night")

    with pytest.raises(ClubNightAnnouncementError, match="forum tag 42"):
        get_applied_tags(forum_channel, 42)
