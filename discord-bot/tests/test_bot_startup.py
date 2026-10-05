import asyncio
import importlib
import logging
from types import SimpleNamespace

import pytest


def _make_forum_channel(
    channel_id: int = 999,
    name: str = "organised-play",
    tag_ids: tuple[int, ...] = (101, 102, 103),
) -> SimpleNamespace:
    tags = [SimpleNamespace(id=tid) for tid in tag_ids]
    return SimpleNamespace(id=channel_id, name=name, available_tags=tags)


def test_check_forum_tag_logs_info_when_tag_id_is_none(caplog) -> None:
    main = importlib.import_module("bot.__main__")
    channel = _make_forum_channel(tag_ids=(101, 102))

    with caplog.at_level(logging.INFO):
        main._check_forum_tag(channel, None)

    assert any("not set" in r.message for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_check_forum_tag_logs_info_when_tag_is_valid(caplog) -> None:
    main = importlib.import_module("bot.__main__")
    channel = _make_forum_channel(tag_ids=(101, 102, 103))

    with caplog.at_level(logging.INFO):
        main._check_forum_tag(channel, 102)

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("102" in r.message for r in info_records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_check_forum_tag_logs_error_when_tag_is_invalid(caplog) -> None:
    main = importlib.import_module("bot.__main__")
    channel = _make_forum_channel(tag_ids=(101, 102, 103))

    with caplog.at_level(logging.ERROR):
        main._check_forum_tag(channel, 999)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 1
    assert "999" in error_records[0].message
    assert "not a valid tag" in error_records[0].message


def test_check_forum_tag_error_message_lists_valid_ids(caplog) -> None:
    main = importlib.import_module("bot.__main__")
    channel = _make_forum_channel(tag_ids=(101, 102))

    with caplog.at_level(logging.ERROR):
        main._check_forum_tag(channel, 999)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 1
    # Valid IDs should appear in the message so operators know what to use.
    assert "101" in error_records[0].message
    assert "102" in error_records[0].message


def test_check_forum_tag_channel_with_no_tags_logs_error(caplog) -> None:
    main = importlib.import_module("bot.__main__")
    channel = _make_forum_channel(tag_ids=())

    with caplog.at_level(logging.ERROR):
        main._check_forum_tag(channel, 101)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 1


def test_club_night_scheduler_starts_once_when_reminder_enabled(monkeypatch) -> None:
    main = importlib.import_module("bot.__main__")
    bot = main.BFWGBot()
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            club_night_reminder_scheduler_enabled=True,
            club_night_announcement_scheduler_enabled=False,
            game_organisation_export_enabled=False,
            gcs_state_upload_enabled=False,
            club_night_reminder_check_interval_seconds=3600,
        ),
    )

    async def fake_scheduler() -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(bot, "_run_club_night_scheduler", fake_scheduler)

    async def _run() -> None:
        bot._ensure_club_night_scheduler()
        first_task = bot._club_night_scheduler_task
        bot._ensure_club_night_scheduler()
        assert bot._club_night_scheduler_task is first_task
        await bot._stop_club_night_scheduler()

    asyncio.run(_run())


def test_club_night_scheduler_starts_when_only_announcement_enabled(monkeypatch) -> None:
    main = importlib.import_module("bot.__main__")
    bot = main.BFWGBot()
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            club_night_reminder_scheduler_enabled=False,
            club_night_announcement_scheduler_enabled=True,
            game_organisation_export_enabled=False,
            gcs_state_upload_enabled=False,
            club_night_reminder_check_interval_seconds=3600,
        ),
    )

    async def fake_scheduler() -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(bot, "_run_club_night_scheduler", fake_scheduler)

    async def _run() -> None:
        bot._ensure_club_night_scheduler()
        assert bot._club_night_scheduler_task is not None
        await bot._stop_club_night_scheduler()

    asyncio.run(_run())


def test_club_night_scheduler_does_not_start_when_disabled(monkeypatch) -> None:
    main = importlib.import_module("bot.__main__")
    bot = main.BFWGBot()
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            club_night_reminder_scheduler_enabled=False,
            club_night_announcement_scheduler_enabled=False,
            game_organisation_export_enabled=False,
            gcs_state_upload_enabled=False,
        ),
    )

    bot._ensure_club_night_scheduler()

    assert bot._club_night_scheduler_task is None


def test_club_night_scheduler_starts_when_only_game_organisation_export_enabled(
    monkeypatch,
) -> None:
    main = importlib.import_module("bot.__main__")
    bot = main.BFWGBot()
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            club_night_reminder_scheduler_enabled=False,
            club_night_announcement_scheduler_enabled=False,
            game_organisation_export_enabled=True,
            gcs_state_upload_enabled=False,
            club_night_reminder_check_interval_seconds=3600,
        ),
    )

    async def fake_scheduler() -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(bot, "_run_club_night_scheduler", fake_scheduler)

    async def _run() -> None:
        bot._ensure_club_night_scheduler()
        assert bot._club_night_scheduler_task is not None
        await bot._stop_club_night_scheduler()

    asyncio.run(_run())


def test_club_night_scheduler_starts_when_only_state_sync_enabled(
    monkeypatch,
) -> None:
    main = importlib.import_module("bot.__main__")
    bot = main.BFWGBot()
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            club_night_reminder_scheduler_enabled=False,
            club_night_announcement_scheduler_enabled=False,
            game_organisation_export_enabled=False,
            gcs_state_upload_enabled=True,
            club_night_reminder_check_interval_seconds=3600,
        ),
    )

    async def fake_scheduler() -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(bot, "_run_club_night_scheduler", fake_scheduler)

    async def _run() -> None:
        bot._ensure_club_night_scheduler()
        assert bot._club_night_scheduler_task is not None
        await bot._stop_club_night_scheduler()

    asyncio.run(_run())


def test_bot_requests_message_content_intent() -> None:
    main = importlib.import_module("bot.__main__")
    bot = main.BFWGBot()

    assert bot.intents.message_content is True


def test_bot_close_cancels_club_night_scheduler() -> None:
    main = importlib.import_module("bot.__main__")
    bot = main.BFWGBot()
    cancelled: list[bool] = []

    async def sleeper() -> None:
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.append(True)

    async def _run() -> None:
        bot._club_night_scheduler_task = asyncio.create_task(sleeper())
        await asyncio.sleep(0)
        await bot.close()

    asyncio.run(_run())

    assert cancelled == [True]


def test_on_app_command_error_logs_unhandled_error(caplog) -> None:
    from discord import app_commands

    main = importlib.import_module("bot.__main__")
    bot = main.BFWGBot()

    interaction = SimpleNamespace(
        command=SimpleNamespace(name="lore random"),
        guild_id=123456789012345678,
        channel_id=999,
        id=888,
        user=SimpleNamespace(id=777),
    )
    error = app_commands.AppCommandError("something broke")

    with caplog.at_level(logging.ERROR):
        asyncio.run(bot.on_app_command_error(interaction, error))

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 1
    assert "lore random" in error_records[0].message
