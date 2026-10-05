import importlib
from types import SimpleNamespace


def test_admin_runtime_guard_allows_discord_administrator(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")
    monkeypatch.setattr(admin, "settings", SimpleNamespace(bot_owner_user_ids=frozenset()))
    interaction = SimpleNamespace(
        user=SimpleNamespace(
            id=123,
            guild_permissions=SimpleNamespace(administrator=True),
        )
    )

    assert admin._is_admin_or_owner(interaction) is True


def test_admin_runtime_guard_allows_configured_owner(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")
    monkeypatch.setattr(admin, "settings", SimpleNamespace(bot_owner_user_ids=frozenset({123})))
    interaction = SimpleNamespace(
        user=SimpleNamespace(
            id=123,
            guild_permissions=SimpleNamespace(administrator=False),
        )
    )

    assert admin._is_admin_or_owner(interaction) is True


def test_admin_runtime_guard_rejects_unprivileged_user(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    admin = importlib.import_module("bot.commands.admin")
    monkeypatch.setattr(admin, "settings", SimpleNamespace(bot_owner_user_ids=frozenset({456})))
    interaction = SimpleNamespace(
        user=SimpleNamespace(
            id=123,
            guild_permissions=SimpleNamespace(administrator=False),
        )
    )

    assert admin._is_admin_or_owner(interaction) is False
