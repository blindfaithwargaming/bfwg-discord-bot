import asyncio
from types import SimpleNamespace

import discord
from discord import app_commands

from bot.commands import register_commands
from bot.services.command_docs import (
    COMMAND_DOCS_TITLE,
    CommandDoc,
    CommandDocsError,
    CommandParamDoc,
    collect_command_docs,
    format_command_docs,
    publish_command_docs,
)
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT


def _real_tree() -> app_commands.CommandTree:
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    register_commands(tree)
    return tree


def test_collect_excludes_admin_only_commands_and_walks_groups() -> None:
    docs = collect_command_docs(_real_tree())
    names = [doc.name for doc in docs]

    assert not any(name == "admin" or name.startswith("admin ") for name in names)
    assert "ping" not in names
    assert "stadium-event" not in names
    assert "post-club-night" not in names
    assert "club-night next" not in names
    assert "club-night pairings" not in names
    assert "club-night preview" not in names
    assert "club-night remind" not in names
    assert "club-night signups" not in names
    for admin_command in (
        "club-night add-player",
        "club-night remove-player",
        "club-night set-side",
        "club-night set-format",
        "club-night lock-pair",
        "club-night avoid-pair",
        "club-night set-bye",
        "club-night clear-signups",
        "club-night preview-pairings",
        "club-night signup-info",
    ):
        assert admin_command not in names
    assert "club-night signup" in names
    assert "rules query" in names
    assert names == sorted(names)


def test_collect_captures_parameters() -> None:
    docs = collect_command_docs(_real_tree())
    by_name = {doc.name: doc for doc in docs}

    query_params = by_name["rules query"].parameters
    assert [(p.name, p.required) for p in query_params] == [("query", True)]

    signup_params = by_name["club-night signup"].parameters
    assert ("side", False) in [(p.name, p.required) for p in signup_params]
    assert ("format", False) in [(p.name, p.required) for p in signup_params]


def test_collect_honours_custom_exclude() -> None:
    docs = collect_command_docs(_real_tree(), exclude=frozenset({"admin", "club-night"}))
    names = [doc.name for doc in docs]

    assert not any(name.startswith("club-night") for name in names)
    assert "rules query" in names


def test_format_real_tree_chunks_within_limit() -> None:
    chunks = format_command_docs(collect_command_docs(_real_tree()))

    assert chunks
    assert chunks[0].startswith(COMMAND_DOCS_TITLE)
    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)


def test_format_renders_parameters() -> None:
    docs = [
        CommandDoc(
            name="club-night signup",
            description="Sign up for the next club night.",
            parameters=(
                CommandParamDoc("side", "Loyalist, Traitor, or Either.", required=False),
            ),
        )
    ]

    chunks = format_command_docs(docs, header="HEADER")

    assert len(chunks) == 1
    body = chunks[0]
    assert "**/club-night signup** — Sign up for the next club night." in body
    assert "`side` *(optional)* — Loyalist, Traitor, or Either." in body


def test_format_splits_when_over_limit() -> None:
    docs = [
        CommandDoc(name=f"command-{i}", description="x" * 500, parameters=())
        for i in range(10)
    ]

    chunks = format_command_docs(docs, header="HEADER")

    assert len(chunks) > 1
    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)


class _FakeMessage:
    def __init__(self, message_id: int, author_id: int) -> None:
        self.id = message_id
        self.author = SimpleNamespace(id=author_id)
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


class _FakeChannel:
    def __init__(self, existing: list[_FakeMessage]) -> None:
        self._existing = existing
        self.sent: list[dict[str, object]] = []
        self._next_id = 1000

    def history(self, *, limit: int):
        async def _iter():
            for message in self._existing[:limit]:
                yield message

        return _iter()

    async def send(self, content: str, *, allowed_mentions: object = None) -> _FakeMessage:
        self._next_id += 1
        self.sent.append({"content": content, "allowed_mentions": allowed_mentions})
        return _FakeMessage(self._next_id, author_id=7)


class _FakeBot:
    def __init__(self, channel: object, *, user_id: int | None = 7) -> None:
        self._channel = channel
        self.user = None if user_id is None else SimpleNamespace(id=user_id)

    def get_channel(self, channel_id: int) -> object:
        return self._channel

    async def fetch_channel(self, channel_id: int) -> object:
        return self._channel


def test_publish_replaces_bot_messages_only() -> None:
    bot_message = _FakeMessage(1, author_id=7)
    human_message = _FakeMessage(2, author_id=99)
    other_bot_message = _FakeMessage(3, author_id=7)
    channel = _FakeChannel([bot_message, human_message, other_bot_message])
    bot = _FakeBot(channel, user_id=7)

    result = asyncio.run(publish_command_docs(bot, 123, _real_tree()))

    assert bot_message.deleted is True
    assert other_bot_message.deleted is True
    assert human_message.deleted is False
    assert result.deleted == 2
    assert result.status == "published"
    assert len(result.message_ids) == len(channel.sent)
    assert channel.sent
    assert channel.sent[0]["content"].startswith(COMMAND_DOCS_TITLE)
    assert channel.sent[0]["allowed_mentions"] is not None


def test_publish_without_bot_user_deletes_nothing() -> None:
    bot_message = _FakeMessage(1, author_id=7)
    channel = _FakeChannel([bot_message])
    bot = _FakeBot(channel, user_id=None)

    result = asyncio.run(
        publish_command_docs(bot, 123, SimpleNamespace(get_commands=lambda: []))
    )

    assert bot_message.deleted is False
    assert result.deleted == 0


def test_publish_raises_for_non_text_channel() -> None:
    channel = SimpleNamespace()  # no send / history
    bot = _FakeBot(channel, user_id=7)

    try:
        asyncio.run(publish_command_docs(bot, 123, SimpleNamespace(get_commands=lambda: [])))
    except CommandDocsError as exc:
        assert "123" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected CommandDocsError")
