import importlib
from datetime import time as dt_time
from pathlib import Path

import pytest

from bot.config import (
    Settings,
    _BATTLEFIELDS_ALLOWED_HOSTS,
    _STADIUM_ALLOWED_HOSTS,
    _validate_fetch_url,
    _validate_int_bounds,
    validate_feedback_state_path,
    validate_games_state_path,
    validate_game_organisation_export_path,
    validate_gcs_state_bucket,
    validate_local_time,
    validate_pairing_constraints_state_path,
    validate_pairing_history_state_path,
    validate_reminder_state_path,
    validate_signup_state_path,
    validate_timezone,
)


def test_default_summaries_dir_prefers_directory_with_markdown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    empty_summaries = tmp_path / "empty"
    populated_summaries = tmp_path / "populated"
    empty_summaries.mkdir()
    populated_summaries.mkdir()
    (populated_summaries / "README.md").write_text("# Summaries\n", encoding="utf-8")

    assert config.default_summaries_dir((empty_summaries, populated_summaries)) == str(
        populated_summaries
    )


def test_default_summaries_dir_falls_back_to_existing_empty_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    empty_summaries = tmp_path / "empty"
    missing_summaries = tmp_path / "missing"
    empty_summaries.mkdir()

    assert config.default_summaries_dir((missing_summaries, empty_summaries)) == str(
        empty_summaries
    )


def test_bool_env_defaults_when_unset(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.delenv("DISCORD_SYNC_COMMANDS_ON_STARTUP", raising=False)

    assert config.bool_env("DISCORD_SYNC_COMMANDS_ON_STARTUP") is False
    assert config.bool_env("DISCORD_SYNC_COMMANDS_ON_STARTUP", default=True) is True


@pytest.mark.parametrize("value", ["1", "true", "yes", "y", "on", "True", "YES", "ON"])
def test_bool_env_accepts_all_truthy_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.setenv("DISCORD_SYNC_COMMANDS_ON_STARTUP", value)

    assert config.bool_env("DISCORD_SYNC_COMMANDS_ON_STARTUP") is True


@pytest.mark.parametrize("value", ["0", "false", "no", "n", "off", "False", "NO", "OFF"])
def test_bool_env_accepts_all_falsy_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.setenv("DISCORD_SYNC_COMMANDS_ON_STARTUP", value)

    assert config.bool_env("DISCORD_SYNC_COMMANDS_ON_STARTUP") is False


def test_bool_env_rejects_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.setenv("DISCORD_SYNC_COMMANDS_ON_STARTUP", "sometimes")

    with pytest.raises(RuntimeError, match="boolean"):
        config.bool_env("DISCORD_SYNC_COMMANDS_ON_STARTUP")


def test_optional_int_set_env_accepts_comma_separated_ids(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.setenv("BOT_OWNER_USER_IDS", "123, 456")

    assert config.optional_int_set_env("BOT_OWNER_USER_IDS") == frozenset({123, 456})


def test_optional_int_set_env_rejects_invalid_ids(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.setenv("BOT_OWNER_USER_IDS", "123, nope")

    with pytest.raises(RuntimeError, match="comma-separated"):
        config.optional_int_set_env("BOT_OWNER_USER_IDS")


def test_validate_summaries_dir_accepts_allowed_root_child(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    summaries_root = tmp_path / "summaries"
    child = summaries_root / "Heresy Black Books"
    child.mkdir(parents=True)

    assert config.validate_summaries_dir(str(child), allowed_roots=(summaries_root,)) == str(
        child.resolve()
    )


def test_validate_summaries_dir_rejects_unexpected_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    summaries_root = tmp_path / "summaries"
    other_root = tmp_path / "other"
    summaries_root.mkdir()
    other_root.mkdir()

    with pytest.raises(RuntimeError, match="expected summaries root"):
        config.validate_summaries_dir(str(other_root), allowed_roots=(summaries_root,))


def test_validate_announcement_template_path_accepts_file_inside_allowed_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    content_root = tmp_path / "content"
    content_root.mkdir()
    template = content_root / "announcement.md"
    template.write_text("# Announcement\n", encoding="utf-8")

    result = config.validate_announcement_template_path(
        str(template), allowed_roots=(content_root,)
    )

    assert result == str(template.resolve())


def test_validate_announcement_template_path_rejects_file_outside_allowed_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    content_root = tmp_path / "content"
    content_root.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    outside_file = other_dir / "secret.md"
    outside_file.write_text("secret", encoding="utf-8")

    with pytest.raises(RuntimeError, match="expected content root"):
        config.validate_announcement_template_path(
            str(outside_file), allowed_roots=(content_root,)
        )


def test_validate_announcement_template_path_rejects_missing_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    content_root = tmp_path / "content"
    content_root.mkdir()
    missing = content_root / "missing.md"

    with pytest.raises(RuntimeError, match="does not exist"):
        config.validate_announcement_template_path(
            str(missing), allowed_roots=(content_root,)
        )


def test_validate_announcement_template_path_rejects_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    content_root = tmp_path / "content"
    subdir = content_root / "subdir"
    subdir.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="must be a file"):
        config.validate_announcement_template_path(
            str(subdir), allowed_roots=(content_root,)
        )


def test_validate_summaries_dir_rejects_missing_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    missing = tmp_path / "missing"

    with pytest.raises(RuntimeError, match="does not exist"):
        config.validate_summaries_dir(str(missing), allowed_roots=(tmp_path,))


def test_validate_summaries_dir_rejects_file_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    file_path = tmp_path / "not_a_dir.txt"
    file_path.write_text("hello", encoding="utf-8")

    with pytest.raises(RuntimeError, match="must be a directory"):
        config.validate_summaries_dir(str(file_path), allowed_roots=(tmp_path,))


def test_default_summaries_dir_falls_back_to_first_candidate_when_none_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    missing_a = tmp_path / "missing_a"
    missing_b = tmp_path / "missing_b"

    assert config.default_summaries_dir((missing_a, missing_b)) == str(missing_a)


def test_require_env_returns_value_when_set(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.setenv("TEST_REQUIRE_VAR", "expected-value")

    assert config.require_env("TEST_REQUIRE_VAR") == "expected-value"


def test_require_env_raises_when_missing(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.delenv("TEST_REQUIRE_VAR", raising=False)

    with pytest.raises(RuntimeError, match="Missing required environment variable"):
        config.require_env("TEST_REQUIRE_VAR")


def test_optional_int_env_returns_none_when_unset(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.delenv("TEST_OPTIONAL_INT", raising=False)

    assert config.optional_int_env("TEST_OPTIONAL_INT") is None


def test_optional_int_env_returns_int_when_set(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.setenv("TEST_OPTIONAL_INT", "42")

    assert config.optional_int_env("TEST_OPTIONAL_INT") == 42


def test_optional_int_env_raises_on_non_integer(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.setenv("TEST_OPTIONAL_INT", "notanint")

    with pytest.raises(RuntimeError, match="must be an integer"):
        config.optional_int_env("TEST_OPTIONAL_INT")


def test_optional_int_set_env_returns_empty_when_unset(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    config = importlib.import_module("bot.config")
    monkeypatch.delenv("BOT_OWNER_USER_IDS", raising=False)

    assert config.optional_int_set_env("BOT_OWNER_USER_IDS") == frozenset()


def test_settings_from_env_raises_when_token_missing(monkeypatch) -> None:
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="Missing required environment variable"):
        Settings.from_env()


def test_settings_from_env_reads_optional_fields(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "999888777")
    monkeypatch.setenv("DISCORD_SYNC_COMMANDS_ON_STARTUP", "true")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.discord_token == "test-token"
    assert s.discord_guild_id == 999888777
    assert s.sync_commands_on_startup is True


def test_settings_constructed_directly_needs_no_env() -> None:
    s = Settings(
        discord_token="direct-token",
        discord_guild_id=None,
        bot_owner_user_ids=frozenset(),
        sync_commands_on_startup=False,
        summaries_dir="/some/path",
        cardiff_stadium_events_url="https://example.com",
        cardiff_stadium_events_timeout_seconds=5,
        cardiff_stadium_events_cache_seconds=900,
        cardiff_stadium_events_max_bytes=1_000_000,
        battlefields_events_url="https://example.com",
        club_night_forum_channel_id=123,
        club_night_forum_tag_id=None,
        club_night_announcement_template_path="/some/template.md",
        club_night_reminder_template_path="/some/reminder.md",
    )

    assert s.discord_token == "direct-token"
    assert s.discord_guild_id is None


# --- Club night reminder scheduler settings ---


def test_validate_local_time_accepts_hh_mm() -> None:
    assert validate_local_time("19:00", "TEST_TIME", "08:00") == dt_time(19, 0)


@pytest.mark.parametrize("value", ["7:00", "24:00", "19:60", "19", "19:00:00", "nope"])
def test_validate_local_time_rejects_invalid_values(value: str) -> None:
    with pytest.raises(RuntimeError, match="HH:MM"):
        validate_local_time(value, "TEST_TIME", "19:00")


def test_validate_timezone_accepts_iana_timezone() -> None:
    assert validate_timezone("Europe/London", "TEST_TZ", "UTC") == "Europe/London"


def test_validate_reminder_state_path_rejects_blank() -> None:
    with pytest.raises(RuntimeError, match="must not be blank"):
        validate_reminder_state_path("   ", scheduler_enabled=False)


def test_validate_reminder_state_path_skips_probe_when_scheduler_disabled(
    tmp_path: Path,
) -> None:
    target = tmp_path / "never-created" / "posts.json"

    result = validate_reminder_state_path(str(target), scheduler_enabled=False)

    assert result == str(target)
    assert not target.parent.exists()


def test_validate_reminder_state_path_creates_parent_when_enabled(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "posts.json"

    result = validate_reminder_state_path(str(target), scheduler_enabled=True)

    assert result == str(target)
    assert target.parent.is_dir()
    assert not any(target.parent.iterdir())


def test_validate_reminder_state_path_rejects_unwritable_parent(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    target = blocker / "posts.json"

    with pytest.raises(RuntimeError, match="CLUB_NIGHT_REMINDER_STATE_PATH"):
        validate_reminder_state_path(str(target), scheduler_enabled=True)


def test_validate_reminder_state_path_rejects_directory_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state-dir"
    target.mkdir()

    with pytest.raises(RuntimeError, match="must be a file"):
        validate_reminder_state_path(str(target), scheduler_enabled=True)


def test_settings_from_env_rejects_unwritable_state_path_when_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_CHANNEL_ID", "123456789012345678")
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_STATE_PATH", str(blocker / "posts.json"))
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    with pytest.raises(RuntimeError, match="CLUB_NIGHT_REMINDER_STATE_PATH"):
        Settings.from_env()


def test_validate_signup_state_path_rejects_blank() -> None:
    with pytest.raises(RuntimeError, match="CLUB_NIGHT_SIGNUP_STATE_PATH"):
        validate_signup_state_path("   ")


def test_validate_signup_state_path_skips_filesystem_probe(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "club_night_signups.json"

    result = validate_signup_state_path(str(target))

    assert result == str(target)
    assert not target.parent.exists()


def test_settings_from_env_reads_signup_state_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    state_path = str(tmp_path / "state" / "club_night_signups.json")
    monkeypatch.setenv("CLUB_NIGHT_SIGNUP_STATE_PATH", state_path)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.club_night_signup_state_path == state_path


def test_settings_from_env_signup_state_path_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("CLUB_NIGHT_SIGNUP_STATE_PATH", raising=False)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.club_night_signup_state_path == "/app/state/club_night_signups.json"


def test_validate_pairing_history_state_path_rejects_blank() -> None:
    with pytest.raises(RuntimeError, match="CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH"):
        validate_pairing_history_state_path("   ")


def test_validate_pairing_history_state_path_skips_filesystem_probe(
    tmp_path: Path,
) -> None:
    target = tmp_path / "missing" / "club_night_pairing_history.json"

    result = validate_pairing_history_state_path(str(target))

    assert result == str(target)
    assert not target.parent.exists()


def test_settings_from_env_reads_pairing_history_config(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    state_path = str(tmp_path / "state" / "club_night_pairing_history.json")
    monkeypatch.setenv("CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH", state_path)
    monkeypatch.setenv("CLUB_NIGHT_PAIRING_LOOKBACK", "4")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.club_night_pairing_history_state_path == state_path
    assert s.club_night_pairing_lookback == 4


def test_settings_from_env_pairing_history_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_PAIRING_LOOKBACK", raising=False)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert (
        s.club_night_pairing_history_state_path
        == "/app/state/club_night_pairing_history.json"
    )
    assert s.club_night_pairing_lookback == 2


def test_validate_pairing_constraints_state_path_rejects_blank() -> None:
    with pytest.raises(RuntimeError, match="CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH"):
        validate_pairing_constraints_state_path("   ")


def test_validate_pairing_constraints_state_path_skips_filesystem_probe(
    tmp_path: Path,
) -> None:
    target = tmp_path / "missing" / "constraints.json"

    result = validate_pairing_constraints_state_path(str(target))

    assert result == str(target)
    assert not target.parent.exists()


def test_settings_from_env_reads_pairing_constraints_state_path(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    state_path = str(tmp_path / "state" / "constraints.json")
    monkeypatch.setenv("CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH", state_path)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.club_night_pairing_constraints_state_path == state_path


def test_settings_from_env_pairing_constraints_state_path_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH", raising=False)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert (
        s.club_night_pairing_constraints_state_path
        == "/app/state/club_night_pairing_constraints.json"
    )


def test_validate_feedback_state_path_rejects_blank() -> None:
    with pytest.raises(RuntimeError, match="FEEDBACK_STATE_PATH"):
        validate_feedback_state_path("   ")


def test_validate_feedback_state_path_skips_filesystem_probe(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "feedback.json"

    result = validate_feedback_state_path(str(target))

    assert result == str(target)
    assert not target.parent.exists()


def test_settings_from_env_reads_feedback_state_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    state_path = str(tmp_path / "state" / "feedback.json")
    monkeypatch.setenv("FEEDBACK_STATE_PATH", state_path)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.feedback_state_path == state_path


def test_settings_from_env_feedback_state_path_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("FEEDBACK_STATE_PATH", raising=False)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.feedback_state_path == "/app/state/feedback.json"


def test_settings_from_env_accepts_pairing_lookback_zero(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_PAIRING_LOOKBACK", "0")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    assert Settings.from_env().club_night_pairing_lookback == 0


@pytest.mark.parametrize("value", ["-1", "11"])
def test_settings_from_env_rejects_pairing_lookback_outside_bounds(
    monkeypatch, value: str
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_PAIRING_LOOKBACK", value)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    with pytest.raises(RuntimeError, match="CLUB_NIGHT_PAIRING_LOOKBACK"):
        Settings.from_env()


def test_validate_timezone_rejects_unknown_timezone() -> None:
    with pytest.raises(RuntimeError, match="valid IANA timezone"):
        validate_timezone("Not/AZone", "TEST_TZ", "Europe/London")


def test_settings_from_env_scheduler_defaults_disabled(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_REMINDER_CHANNEL_ID", raising=False)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.club_night_reminder_scheduler_enabled is False
    assert s.club_night_reminder_channel_id is None
    assert s.club_night_reminder_days_before == 3
    assert s.club_night_reminder_time == dt_time(19, 0)
    assert s.club_night_reminder_timezone == "Europe/London"
    assert s.club_night_reminder_check_interval_seconds == 3600
    assert s.club_night_announcement_scheduler_enabled is False
    assert s.club_night_announcement_time == dt_time(13, 0)


def test_settings_from_env_scheduler_requires_channel_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED", "true")
    monkeypatch.delenv("CLUB_NIGHT_REMINDER_CHANNEL_ID", raising=False)

    with pytest.raises(RuntimeError, match="CLUB_NIGHT_REMINDER_CHANNEL_ID"):
        Settings.from_env()


def test_settings_from_env_reads_scheduler_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_CHANNEL_ID", "123456789012345678")
    monkeypatch.setenv(
        "CLUB_NIGHT_REMINDER_STATE_PATH", str(tmp_path / "state" / "posts.json")
    )
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_DAYS_BEFORE", "5")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_TIME", "20:15")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_TIMEZONE", "UTC")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS", "7200")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.club_night_reminder_scheduler_enabled is True
    assert s.club_night_reminder_state_path == str(tmp_path / "state" / "posts.json")
    assert s.club_night_reminder_channel_id == 123456789012345678
    assert s.club_night_reminder_days_before == 5
    assert s.club_night_reminder_time == dt_time(20, 15)
    assert s.club_night_reminder_timezone == "UTC"
    assert s.club_night_reminder_check_interval_seconds == 7200


def test_settings_from_env_reads_announcement_scheduler_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("CLUB_NIGHT_ANNOUNCEMENT_TIME", "14:30")
    monkeypatch.setenv(
        "CLUB_NIGHT_REMINDER_STATE_PATH", str(tmp_path / "state" / "posts.json")
    )
    monkeypatch.delenv("CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_REMINDER_CHANNEL_ID", raising=False)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.club_night_announcement_scheduler_enabled is True
    assert s.club_night_announcement_time == dt_time(14, 30)
    assert (tmp_path / "state").is_dir()


def test_settings_from_env_rejects_invalid_announcement_time(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_ANNOUNCEMENT_TIME", "1pm")

    with pytest.raises(RuntimeError, match="CLUB_NIGHT_ANNOUNCEMENT_TIME"):
        Settings.from_env()


def test_settings_from_env_probes_state_path_when_announcement_scheduler_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED", "true")
    monkeypatch.delenv("CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_REMINDER_CHANNEL_ID", raising=False)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_STATE_PATH", str(blocker / "posts.json"))
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    with pytest.raises(RuntimeError, match="CLUB_NIGHT_REMINDER_STATE_PATH"):
        Settings.from_env()


@pytest.mark.parametrize("value", ["-1", "31"])
def test_settings_from_env_rejects_scheduler_days_outside_bounds(
    monkeypatch,
    value: str,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_DAYS_BEFORE", value)

    with pytest.raises(RuntimeError, match="CLUB_NIGHT_REMINDER_DAYS_BEFORE"):
        Settings.from_env()


def test_settings_from_env_accepts_same_day_scheduler_reminder(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_DAYS_BEFORE", "0")

    assert Settings.from_env().club_night_reminder_days_before == 0


@pytest.mark.parametrize("value", ["299", "86401"])
def test_settings_from_env_rejects_scheduler_interval_outside_bounds(
    monkeypatch,
    value: str,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS", value)

    with pytest.raises(RuntimeError, match="CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS"):
        Settings.from_env()


# --- _validate_fetch_url ---


def test_validate_fetch_url_accepts_valid_stadium_url() -> None:
    url = "https://www.cardiffcitystadiumevents.co.uk/"
    assert _validate_fetch_url(url, "TEST", _STADIUM_ALLOWED_HOSTS) == url


def test_validate_fetch_url_accepts_valid_battlefields_url() -> None:
    url = "https://www.thebattlefields.co.uk/events-at-the-battlefields?venue=0&category=22"
    assert _validate_fetch_url(url, "TEST", _BATTLEFIELDS_ALLOWED_HOSTS) == url


def test_validate_fetch_url_rejects_http_scheme() -> None:
    with pytest.raises(RuntimeError, match="must use https"):
        _validate_fetch_url(
            "http://www.cardiffcitystadiumevents.co.uk/",
            "TEST",
            _STADIUM_ALLOWED_HOSTS,
        )


def test_validate_fetch_url_rejects_disallowed_hostname() -> None:
    with pytest.raises(RuntimeError, match="hostname must be one of"):
        _validate_fetch_url(
            "https://evil.example.com/",
            "TEST",
            _STADIUM_ALLOWED_HOSTS,
        )


def test_validate_fetch_url_rejects_credentials_in_url() -> None:
    with pytest.raises(RuntimeError, match="must not include credentials"):
        _validate_fetch_url(
            "https://user:pass@www.cardiffcitystadiumevents.co.uk/",
            "TEST",
            _STADIUM_ALLOWED_HOSTS,
        )


def test_validate_fetch_url_rejects_battlefields_url_on_stadium_allowlist() -> None:
    with pytest.raises(RuntimeError, match="hostname must be one of"):
        _validate_fetch_url(
            "https://www.thebattlefields.co.uk/",
            "TEST",
            _STADIUM_ALLOWED_HOSTS,
        )


# --- _validate_int_bounds ---


def test_validate_int_bounds_accepts_value_within_range() -> None:
    assert _validate_int_bounds(5, "TEST", 1, 30) == 5


def test_validate_int_bounds_accepts_minimum_boundary() -> None:
    assert _validate_int_bounds(1, "TEST", 1, 30) == 1


def test_validate_int_bounds_accepts_maximum_boundary() -> None:
    assert _validate_int_bounds(30, "TEST", 1, 30) == 30


def test_validate_int_bounds_rejects_value_below_minimum() -> None:
    with pytest.raises(RuntimeError, match="must be between 1 and 30"):
        _validate_int_bounds(0, "TEST", 1, 30)


def test_validate_int_bounds_rejects_value_above_maximum() -> None:
    with pytest.raises(RuntimeError, match="must be between 1 and 30"):
        _validate_int_bounds(31, "TEST", 1, 30)


# --- Settings.from_env() URL and bounds validation ---


def test_settings_from_env_rejects_stadium_url_with_wrong_hostname(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_URL", "https://evil.example.com/")

    with pytest.raises(RuntimeError, match="hostname must be one of"):
        Settings.from_env()


def test_settings_from_env_rejects_stadium_url_without_https(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_URL", "http://www.cardiffcitystadiumevents.co.uk/")

    with pytest.raises(RuntimeError, match="must use https"):
        Settings.from_env()


def test_settings_from_env_rejects_battlefields_url_with_wrong_hostname(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("BATTLEFIELDS_EVENTS_URL", "https://evil.example.com/")

    with pytest.raises(RuntimeError, match="hostname must be one of"):
        Settings.from_env()


def test_settings_from_env_rejects_timeout_above_maximum(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_TIMEOUT_SECONDS", "999")

    with pytest.raises(RuntimeError, match="must be between"):
        Settings.from_env()


def test_settings_from_env_rejects_timeout_below_minimum(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_TIMEOUT_SECONDS", "-1")

    with pytest.raises(RuntimeError, match="must be between"):
        Settings.from_env()


def test_settings_from_env_rejects_cache_below_minimum(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_CACHE_SECONDS", "5")

    with pytest.raises(RuntimeError, match="must be between"):
        Settings.from_env()


def test_settings_from_env_rejects_max_bytes_below_minimum(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_MAX_BYTES", "100")

    with pytest.raises(RuntimeError, match="must be between"):
        Settings.from_env()


def test_settings_from_env_accepts_valid_custom_timeout(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_TIMEOUT_SECONDS", "10")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.cardiff_stadium_events_timeout_seconds == 10


def test_settings_from_env_rejects_timeout_zero(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_TIMEOUT_SECONDS", "0")

    with pytest.raises(RuntimeError, match="must be between"):
        Settings.from_env()


def test_settings_from_env_rejects_cache_zero(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_CACHE_SECONDS", "0")

    with pytest.raises(RuntimeError, match="must be between"):
        Settings.from_env()


def test_settings_from_env_rejects_max_bytes_zero(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("CARDIFF_STADIUM_EVENTS_MAX_BYTES", "0")

    with pytest.raises(RuntimeError, match="must be between"):
        Settings.from_env()


def test_settings_from_env_uses_defaults_when_numeric_env_unset(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_CACHE_SECONDS", raising=False)
    monkeypatch.delenv("CARDIFF_STADIUM_EVENTS_MAX_BYTES", raising=False)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.cardiff_stadium_events_timeout_seconds == 5
    assert s.cardiff_stadium_events_cache_seconds == 900
    assert s.cardiff_stadium_events_max_bytes == 1_000_000


def test_settings_from_env_reads_reminder_state_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)
    state_path = str(tmp_path / "markers.json")
    monkeypatch.setenv("CLUB_NIGHT_REMINDER_STATE_PATH", state_path)

    s = Settings.from_env()

    assert s.club_night_reminder_state_path == state_path


def test_settings_from_env_reminder_state_path_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_REMINDER_STATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.club_night_reminder_state_path == "/app/state/scheduled_posts.json"


# --- game organisation export ---


def test_validate_game_organisation_export_path_rejects_blank() -> None:
    with pytest.raises(RuntimeError, match="must not be blank"):
        validate_game_organisation_export_path("   ")


def test_validate_game_organisation_export_path_skips_probe_when_disabled() -> None:
    assert (
        validate_game_organisation_export_path("/nonexistent/state/export.json")
        == "/nonexistent/state/export.json"
    )


def test_validate_game_organisation_export_path_probes_when_enabled(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "state" / "export.json")

    assert validate_game_organisation_export_path(path, export_enabled=True) == path
    assert (tmp_path / "state").is_dir()


def test_validate_game_organisation_export_path_rejects_directory_when_enabled(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="must be a file"):
        validate_game_organisation_export_path(str(tmp_path), export_enabled=True)


def test_settings_from_env_game_organisation_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)
    monkeypatch.delenv("GAME_ORGANISATION_EXPORT_ENABLED", raising=False)
    monkeypatch.delenv("GAME_ORGANISATION_CHANNEL_ID", raising=False)
    monkeypatch.delenv("GAME_ORGANISATION_EXPORT_PATH", raising=False)
    monkeypatch.delenv("GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS", raising=False)

    s = Settings.from_env()

    assert s.game_organisation_export_enabled is False
    assert s.game_organisation_channel_id is None
    assert s.game_organisation_export_path == "/app/state/game_organisation_export.json"
    assert s.game_organisation_export_max_age_hours == 24


def test_settings_from_env_game_organisation_enabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)
    monkeypatch.setenv("GAME_ORGANISATION_EXPORT_ENABLED", "true")
    monkeypatch.setenv("GAME_ORGANISATION_CHANNEL_ID", "1370992608918110258")
    export_path = str(tmp_path / "state" / "game_organisation_export.json")
    monkeypatch.setenv("GAME_ORGANISATION_EXPORT_PATH", export_path)
    monkeypatch.setenv("GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS", "12")

    s = Settings.from_env()

    assert s.game_organisation_export_enabled is True
    assert s.game_organisation_channel_id == 1370992608918110258
    assert s.game_organisation_export_path == export_path
    assert s.game_organisation_export_max_age_hours == 12
    assert (tmp_path / "state").is_dir()


def test_settings_from_env_game_organisation_enabled_requires_channel(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("GAME_ORGANISATION_EXPORT_ENABLED", "true")
    monkeypatch.delenv("GAME_ORGANISATION_CHANNEL_ID", raising=False)

    with pytest.raises(RuntimeError, match="GAME_ORGANISATION_CHANNEL_ID is required"):
        Settings.from_env()


def test_settings_from_env_gcs_state_upload_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)
    monkeypatch.delenv("GCS_STATE_UPLOAD_ENABLED", raising=False)
    monkeypatch.delenv("GCS_STATE_BUCKET", raising=False)

    s = Settings.from_env()

    assert s.gcs_state_upload_enabled is False
    assert s.gcs_state_bucket is None


def test_settings_from_env_gcs_state_upload_enabled(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)
    monkeypatch.setenv("GCS_STATE_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("GCS_STATE_BUCKET", "example-bfwg-bot-state")

    s = Settings.from_env()

    assert s.gcs_state_upload_enabled is True
    assert s.gcs_state_bucket == "example-bfwg-bot-state"


def test_settings_from_env_gcs_state_upload_enabled_requires_bucket(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("GCS_STATE_UPLOAD_ENABLED", "true")
    monkeypatch.delenv("GCS_STATE_BUCKET", raising=False)

    with pytest.raises(RuntimeError, match="GCS_STATE_BUCKET is required"):
        Settings.from_env()


def test_validate_gcs_state_bucket_strips_scheme_and_whitespace() -> None:
    assert validate_gcs_state_bucket(" gs://example-bucket ") == "example-bucket"
    assert validate_gcs_state_bucket("example-bucket") == "example-bucket"


@pytest.mark.parametrize("value", ["", "   ", "gs://"])
def test_validate_gcs_state_bucket_rejects_blank(value: str) -> None:
    with pytest.raises(RuntimeError, match="GCS_STATE_BUCKET must not be blank"):
        validate_gcs_state_bucket(value)


def test_validate_gcs_state_bucket_rejects_paths() -> None:
    with pytest.raises(RuntimeError, match="bare bucket name"):
        validate_gcs_state_bucket("example-bucket/prefix")


@pytest.mark.parametrize("value", ["0", "169"])
def test_settings_from_env_rejects_game_organisation_max_age_outside_bounds(
    monkeypatch,
    value: str,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("GAME_ORGANISATION_EXPORT_ENABLED", raising=False)
    monkeypatch.setenv("GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS", value)

    with pytest.raises(RuntimeError, match="GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS"):
        Settings.from_env()


def test_validate_games_state_path_rejects_blank() -> None:
    with pytest.raises(RuntimeError, match="CLUB_NIGHT_GAMES_STATE_PATH"):
        validate_games_state_path("   ")


def test_validate_games_state_path_skips_filesystem_probe(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "club_night_games.json"

    result = validate_games_state_path(str(target))

    assert result == str(target)
    assert not target.parent.exists()


def test_settings_from_env_reads_games_state_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    state_path = str(tmp_path / "state" / "club_night_games.json")
    monkeypatch.setenv("CLUB_NIGHT_GAMES_STATE_PATH", state_path)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.club_night_games_state_path == state_path


def test_settings_from_env_games_state_path_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.delenv("CLUB_NIGHT_GAMES_STATE_PATH", raising=False)
    monkeypatch.delenv("SUMMARIES_DIR", raising=False)
    monkeypatch.delenv("CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", raising=False)

    s = Settings.from_env()

    assert s.club_night_games_state_path == "/app/state/club_night_games.json"
