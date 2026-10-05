import importlib
from types import SimpleNamespace


def _diag(**overrides: object) -> dict:
    defaults: dict = dict(
        bot_user="Bot",
        bot_user_id=None,
        summaries_dir="/app/summaries",
        md_count=0,
        command_names=[],
        forum_channel_id=1,
        owner_count=0,
        cache_age=None,
        git_commit="unknown",
    )
    defaults.update(overrides)
    return defaults


def test_build_diagnostics_text_includes_bot_user_and_id(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(
        **_diag(bot_user="BFWGBot#1234", bot_user_id=987654321, md_count=42, command_names=["admin", "ping"], forum_channel_id=1370992608918110258, owner_count=2)
    )

    assert "BFWGBot#1234" in text
    assert "987654321" in text


def test_build_diagnostics_text_omits_id_line_when_none(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(**_diag(bot_user="Unknown"))

    assert "(ID:" not in text


def test_build_diagnostics_text_includes_summaries_path_and_count(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(**_diag(summaries_dir="/app/summaries", md_count=17, forum_channel_id=12345))

    assert "/app/summaries" in text
    assert "17" in text


def test_build_diagnostics_text_includes_command_count_and_names(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(**_diag(command_names=["admin", "lore", "ping"], forum_channel_id=99))

    assert "3 registered" in text
    assert "/admin" in text
    assert "/lore" in text
    assert "/ping" in text


def test_build_diagnostics_text_includes_forum_channel_id(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(**_diag(forum_channel_id=555555))

    assert "555555" in text


def test_build_diagnostics_text_includes_owner_count(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(**_diag(owner_count=3))

    assert "3 configured" in text


def test_build_diagnostics_text_zero_owners_configured(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(**_diag(owner_count=0))

    assert "0 configured" in text


def test_build_diagnostics_text_cache_age_populated(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(**_diag(cache_age=42.0))

    assert "42s ago" in text


def test_build_diagnostics_text_cache_age_not_populated(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(**_diag(cache_age=None))

    assert "not populated" in text


def test_build_diagnostics_text_includes_git_commit(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(**_diag(git_commit="abc1234"))

    assert "abc1234" in text


def test_build_diagnostics_text_git_commit_unknown_fallback(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_diagnostics_text(**_diag(git_commit="unknown"))

    assert "unknown" in text


def test_build_config_check_text_all_pass(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_config_check_text(
        summaries_ok=True,
        template_ok=True,
        forum_channel_ok=True,
        forum_tag_ok=True,
    )

    assert "**Summaries dir:** PASS" in text
    assert "**Announcement template:** PASS" in text
    assert "**Forum channel:** PASS" in text
    assert "**Forum tag:** PASS" in text


def test_build_config_check_text_all_fail(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_config_check_text(
        summaries_ok=False,
        template_ok=False,
        forum_channel_ok=False,
        forum_tag_ok=False,
    )

    assert "**Summaries dir:** FAIL" in text
    assert "**Announcement template:** FAIL" in text
    assert "**Forum channel:** FAIL" in text
    assert "**Forum tag:** FAIL" in text


def test_build_config_check_text_tag_not_configured(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_config_check_text(
        summaries_ok=True,
        template_ok=True,
        forum_channel_ok=True,
        forum_tag_ok=None,
    )

    assert "**Forum tag:** not configured" in text


def test_build_config_check_text_partial_failures(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")

    text = admin._build_config_check_text(
        summaries_ok=True,
        template_ok=False,
        forum_channel_ok=True,
        forum_tag_ok=None,
    )

    assert "**Summaries dir:** PASS" in text
    assert "**Announcement template:** FAIL" in text
    assert "**Forum channel:** PASS" in text
