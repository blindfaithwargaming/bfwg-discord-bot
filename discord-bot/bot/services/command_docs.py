from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import discord

from bot.services.text_utils import DISCORD_MESSAGE_LIMIT, truncate


# Command names that are never published to the community docs post. Entries can
# be top-level names (``admin``) or fully-qualified leaf names
# (``club-night pairings``). Admins use the in-repo documentation instead (see
# CLAUDE.md). Commands whose effective Discord default permissions require
# Administrator are also omitted.
DEFAULT_EXCLUDED_COMMANDS: frozenset[str] = frozenset(
    {
        "admin",
        "club-night add-player",
        "club-night avoid-pair",
        "club-night clear-signups",
        "club-night lock-pair",
        "club-night next",
        "club-night pairings",
        "club-night preview",
        "club-night preview-pairings",
        "club-night signup-info",
        "club-night remove-player",
        "club-night remind",
        "club-night set-bye",
        "club-night set-format",
        "club-night set-side",
        "club-night signups",
    }
)

# First line of the post — also the human marker for the maintained message(s).
COMMAND_DOCS_TITLE = "# 📖 BFWG Bot Commands"
COMMAND_DOCS_HEADER = (
    f"{COMMAND_DOCS_TITLE}\n"
    "Slash commands available to everyone — type `/` in any channel to use them. "
    "Staff-only tools are intentionally omitted."
)

NO_MENTIONS = discord.AllowedMentions.none()

# How far back to look when clearing the bot's previous docs messages. The docs
# channel is expected to be dedicated to this post, so this only needs to cover
# the handful of messages a previous publish produced.
_HISTORY_SCAN_LIMIT = 50


class CommandDocsError(RuntimeError):
    """Raised when the command docs post cannot be published."""


@dataclass(frozen=True)
class CommandParamDoc:
    name: str
    description: str
    required: bool


@dataclass(frozen=True)
class CommandDoc:
    name: str
    description: str
    parameters: tuple[CommandParamDoc, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CommandDocsResult:
    status: str
    reason: str
    message_ids: tuple[int, ...]
    deleted: int


def collect_command_docs(
    tree: Any,
    *,
    exclude: frozenset[str] = DEFAULT_EXCLUDED_COMMANDS,
) -> list[CommandDoc]:
    """Introspect a command tree into a flat, sorted list of leaf-command docs.

    Commands whose qualified name is in ``exclude`` are dropped entirely. Commands
    whose effective Discord default permissions require Administrator are also
    omitted. Groups are walked so each subcommand appears under its fully-qualified
    name (e.g. ``club-night signup``).
    """
    docs: list[CommandDoc] = []

    for command in tree.get_commands():
        if _is_excluded(command, exclude):
            continue
        _collect(command, docs, exclude=exclude, inherited_default_permissions=None)

    docs.sort(key=lambda doc: doc.name)
    return docs


def _collect(
    command: Any,
    docs: list[CommandDoc],
    *,
    exclude: frozenset[str],
    inherited_default_permissions: discord.Permissions | None,
) -> None:
    if _is_excluded(command, exclude):
        return

    command_default_permissions = getattr(command, "default_permissions", None)
    default_permissions = (
        command_default_permissions
        if command_default_permissions is not None
        else inherited_default_permissions
    )

    if _requires_administrator(default_permissions):
        return

    children = getattr(command, "commands", None)

    if children:
        for child in children:
            _collect(
                child,
                docs,
                exclude=exclude,
                inherited_default_permissions=default_permissions,
            )
        return

    parameters = tuple(
        CommandParamDoc(
            name=getattr(parameter, "display_name", None) or parameter.name,
            description=parameter.description or "",
            required=bool(parameter.required),
        )
        for parameter in getattr(command, "parameters", ())
    )

    docs.append(
        CommandDoc(
            name=command.qualified_name,
            description=command.description or "",
            parameters=parameters,
        )
    )


def _is_excluded(command: Any, exclude: frozenset[str]) -> bool:
    qualified_name = getattr(command, "qualified_name", None) or getattr(command, "name", "")
    return qualified_name in exclude


def _requires_administrator(permissions: discord.Permissions | None) -> bool:
    return bool(getattr(permissions, "administrator", False))


def format_command_docs(
    docs: list[CommandDoc],
    *,
    header: str = COMMAND_DOCS_HEADER,
) -> list[str]:
    """Render docs into one or more messages, each within the Discord limit.

    Per-command blocks are never split across messages; they are packed greedily
    so the post uses as few messages as possible.
    """
    blocks = [_format_block(doc) for doc in docs]

    chunks: list[str] = []
    current = header

    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block

        if len(candidate) > DISCORD_MESSAGE_LIMIT and current:
            chunks.append(current)
            current = block
        else:
            current = candidate

    if current:
        chunks.append(current)

    # A single block longer than the limit is pathological, but truncate so a
    # publish can never raise on an over-length message.
    return [truncate(chunk, DISCORD_MESSAGE_LIMIT) for chunk in chunks]


def _format_block(doc: CommandDoc) -> str:
    heading = f"**/{doc.name}**"
    if doc.description:
        heading += f" — {doc.description}"

    lines = [heading]
    for parameter in doc.parameters:
        tag = "required" if parameter.required else "optional"
        line = f"• `{parameter.name}` *({tag})*"
        if parameter.description:
            line += f" — {parameter.description}"
        lines.append(line)

    return "\n".join(lines)


async def publish_command_docs(
    bot_client: Any,
    channel_id: int,
    tree: Any,
    *,
    exclude: frozenset[str] = DEFAULT_EXCLUDED_COMMANDS,
) -> CommandDocsResult:
    """Replace the maintained command-docs post in ``channel_id``.

    Clears the bot's previous messages in the channel (it is expected to be a
    dedicated docs channel) and posts the freshly rendered docs. Raises
    :class:`CommandDocsError` if the channel cannot host the post.
    """
    docs = collect_command_docs(tree, exclude=exclude)
    chunks = format_command_docs(docs)

    channel = await _resolve_text_channel(bot_client, channel_id)
    bot_user_id = getattr(getattr(bot_client, "user", None), "id", None)
    deleted = await _clear_previous_messages(channel, bot_user_id)

    message_ids: list[int] = []
    for chunk in chunks:
        message = await channel.send(chunk, allowed_mentions=NO_MENTIONS)
        message_ids.append(int(getattr(message, "id", 0)))

    return CommandDocsResult(
        status="published",
        reason="published",
        message_ids=tuple(message_ids),
        deleted=deleted,
    )


async def _resolve_text_channel(bot_client: Any, channel_id: int) -> Any:
    channel = bot_client.get_channel(channel_id)
    if channel is None:
        channel = await bot_client.fetch_channel(channel_id)

    if not hasattr(channel, "send") or not hasattr(channel, "history"):
        raise CommandDocsError(
            f"COMMAND_DOCS_CHANNEL_ID {channel_id} cannot host a text post."
        )

    return channel


async def _clear_previous_messages(channel: Any, bot_user_id: int | None) -> int:
    if bot_user_id is None:
        return 0

    deleted = 0
    async for message in channel.history(limit=_HISTORY_SCAN_LIMIT):
        author_id = getattr(getattr(message, "author", None), "id", None)
        if author_id == bot_user_id:
            await message.delete()
            deleted += 1

    return deleted
