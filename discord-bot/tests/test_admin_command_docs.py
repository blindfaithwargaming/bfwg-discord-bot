import asyncio
from types import SimpleNamespace

from bot.command_sync import CommandSyncResult
from bot.commands import admin
from bot.services.command_docs import CommandDocsError, CommandDocsResult


class _CapturingTree:
    def __init__(self) -> None:
        self.commands: list[object] = []

    def add_command(self, command: object) -> None:
        self.commands.append(command)


class _Response:
    def __init__(self) -> None:
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self._done = True

    async def send_message(self, msg: str, *, ephemeral: bool = False) -> None:
        self._done = True


class _Followup:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send(self, content: str, *, ephemeral: bool = False) -> None:
        self.sent.append({"content": content, "ephemeral": ephemeral})


def _sync_callback():
    tree = _CapturingTree()
    admin.register_admin_commands(tree)
    group = tree.commands[0]
    return group.get_command("sync-commands").callback


def _make_interaction(channel_id: int | None) -> SimpleNamespace:
    client_tree = object()
    return SimpleNamespace(
        guild_id=1,
        channel_id=2,
        user=SimpleNamespace(id=42, guild_permissions=SimpleNamespace(administrator=True)),
        response=_Response(),
        followup=_Followup(),
        client=SimpleNamespace(tree=client_tree),
    )


def _settings(channel_id: int | None) -> SimpleNamespace:
    return SimpleNamespace(
        bot_owner_user_ids=frozenset(),
        discord_guild_id=1,
        command_docs_channel_id=channel_id,
    )


def test_update_command_docs_disabled_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(admin, "settings", _settings(None))

    note = asyncio.run(admin._update_command_docs(SimpleNamespace(tree=object())))

    assert note == ""


def test_update_command_docs_success_note(monkeypatch) -> None:
    monkeypatch.setattr(admin, "settings", _settings(555))

    async def fake_publish(client, channel_id, tree):
        assert channel_id == 555
        return CommandDocsResult("published", "published", (1, 2), deleted=1)

    monkeypatch.setattr(admin, "publish_command_docs", fake_publish)

    note = asyncio.run(admin._update_command_docs(SimpleNamespace(tree=object())))

    assert "<#555>" in note
    assert "2 messages" in note


def test_update_command_docs_failure_note(monkeypatch) -> None:
    monkeypatch.setattr(admin, "settings", _settings(555))

    async def fake_publish(client, channel_id, tree):
        raise CommandDocsError("boom")

    monkeypatch.setattr(admin, "publish_command_docs", fake_publish)

    note = asyncio.run(admin._update_command_docs(SimpleNamespace(tree=object())))

    assert "⚠️" in note
    assert "<#555>" in note


def test_sync_commands_appends_docs_note(monkeypatch) -> None:
    monkeypatch.setattr(admin, "settings", _settings(555))

    async def fake_sync(tree, guild_id):
        return CommandSyncResult(scope="guild", command_names=["ping"], guild_id=guild_id)

    async def fake_publish(client, channel_id, tree):
        return CommandDocsResult("published", "published", (1,), deleted=0)

    monkeypatch.setattr(admin, "sync_registered_commands", fake_sync)
    monkeypatch.setattr(admin, "publish_command_docs", fake_publish)

    callback = _sync_callback()
    interaction = _make_interaction(channel_id=555)

    asyncio.run(callback(interaction))

    assert len(interaction.followup.sent) == 1
    content = interaction.followup.sent[0]["content"]
    assert "Synced 1 guild commands for 1" in content
    assert "Updated the command docs post in <#555>" in content


def test_sync_commands_without_docs_channel_has_no_note(monkeypatch) -> None:
    monkeypatch.setattr(admin, "settings", _settings(None))

    async def fake_sync(tree, guild_id):
        return CommandSyncResult(scope="guild", command_names=["ping"], guild_id=guild_id)

    monkeypatch.setattr(admin, "sync_registered_commands", fake_sync)

    callback = _sync_callback()
    interaction = _make_interaction(channel_id=None)

    asyncio.run(callback(interaction))

    content = interaction.followup.sent[0]["content"]
    assert "command docs post" not in content
