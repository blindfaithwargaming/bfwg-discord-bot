import importlib
import logging
from pathlib import Path
from types import SimpleNamespace


def _make_health_settings(tmp_path: Path, **overrides: object) -> SimpleNamespace:
    defaults = dict(
        discord_guild_id=None,
        summaries_dir=str(tmp_path),
        club_night_forum_channel_id=999,
        club_night_announcement_template_path="/app/content/template.md",
        club_night_reminder_template_path="/app/content/club_night_reminder.md",
        cardiff_stadium_events_url="https://cardiffcitystadiumevents.co.uk/",
        battlefields_events_url="https://thebattlefields.co.uk/events",
        game_organisation_export_enabled=False,
        game_organisation_channel_id=None,
        game_organisation_export_path="/app/state/game_organisation_export.json",
        gcs_state_upload_enabled=False,
        gcs_state_bucket=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_log_startup_health_logs_guild_id(monkeypatch, tmp_path: Path, caplog) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(main, "settings", _make_health_settings(tmp_path, discord_guild_id=12345))
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "12345" in caplog.text


def test_log_startup_health_logs_global_when_no_guild(monkeypatch, tmp_path: Path, caplog) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(main, "settings", _make_health_settings(tmp_path))
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "global" in caplog.text


def test_log_startup_health_counts_md_files(monkeypatch, tmp_path: Path, caplog) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    (tmp_path / "alpha.md").write_text("# A", encoding="utf-8")
    (tmp_path / "beta.md").write_text("# B", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(main, "settings", _make_health_settings(tmp_path))
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "2 .md" in caplog.text


def test_log_startup_health_logs_summaries_path(monkeypatch, tmp_path: Path, caplog) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(main, "settings", _make_health_settings(tmp_path))
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert str(tmp_path) in caplog.text


def test_log_startup_health_forum_channel_shown_as_configured(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(main, "settings", _make_health_settings(tmp_path, club_night_forum_channel_id=1111))
    monkeypatch.setenv("CLUB_NIGHT_FORUM_CHANNEL_ID", "1111")
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "1111" in caplog.text
    assert "configured" in caplog.text


def test_log_startup_health_forum_channel_shown_as_default(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(main, "settings", _make_health_settings(tmp_path, club_night_forum_channel_id=2222))
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "2222" in caplog.text
    assert "default" in caplog.text


def test_log_startup_health_logs_announcement_template_path(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(
        main,
        "settings",
        _make_health_settings(tmp_path, club_night_announcement_template_path="/app/content/my-template.md"),
    )
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "/app/content/my-template.md" in caplog.text


def test_log_startup_health_logs_reminder_template_path(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(
        main,
        "settings",
        _make_health_settings(tmp_path, club_night_reminder_template_path="/app/content/my-reminder.md"),
    )
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "/app/content/my-reminder.md" in caplog.text


def test_log_startup_health_stadium_url_shown_as_configured(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(
        main,
        "settings",
        _make_health_settings(tmp_path, cardiff_stadium_events_url="https://custom.example.com/stadium"),
    )
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_URL", "https://custom.example.com/stadium")
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "https://custom.example.com/stadium" in caplog.text
    assert "configured" in caplog.text


def test_log_startup_health_stadium_url_shown_as_default(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(main, "settings", _make_health_settings(tmp_path))
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "Cardiff Stadium events URL" in caplog.text
    assert "default" in caplog.text


def test_log_startup_health_battlefields_url_shown_as_configured(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(
        main,
        "settings",
        _make_health_settings(tmp_path, battlefields_events_url="https://custom.example.com/bf"),
    )
    monkeypatch.setenv("BATTLEFIELDS_EVENTS_URL", "https://custom.example.com/bf")
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "https://custom.example.com/bf" in caplog.text
    assert "configured" in caplog.text


def test_log_startup_health_battlefields_url_shown_as_default(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(main, "settings", _make_health_settings(tmp_path))
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "Battlefields events URL" in caplog.text
    assert "default" in caplog.text


def test_log_startup_health_gcs_state_upload_enabled(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(
        main,
        "settings",
        _make_health_settings(
            tmp_path,
            gcs_state_upload_enabled=True,
            gcs_state_bucket="example-bfwg-bot-state",
        ),
    )
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "GCS state upload: enabled" in caplog.text
    assert "example-bfwg-bot-state" in caplog.text


def test_log_startup_health_gcs_state_upload_disabled(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    main = importlib.import_module("bot.__main__")
    monkeypatch.setattr(main, "settings", _make_health_settings(tmp_path))
    monkeypatch.delenv("CLUB_NIGHT_FORUM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_URL", raising=False)
    monkeypatch.delenv("BATTLEFIELDS_EVENTS_URL", raising=False)

    with caplog.at_level(logging.INFO):
        main._log_startup_health()

    assert "GCS state upload: disabled" in caplog.text
