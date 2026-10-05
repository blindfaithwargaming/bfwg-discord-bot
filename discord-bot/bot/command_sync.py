from dataclasses import dataclass

import discord


@dataclass(frozen=True)
class CommandSyncResult:
    scope: str
    command_names: list[str]
    guild_id: int | None = None


async def sync_registered_commands(
    tree: discord.app_commands.CommandTree,
    guild_id: int | None,
) -> CommandSyncResult:
    if guild_id is not None:
        guild = discord.Object(id=guild_id)
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        return CommandSyncResult(
            scope="guild",
            guild_id=guild_id,
            command_names=sorted(command.name for command in synced),
        )

    synced = await tree.sync()
    return CommandSyncResult(
        scope="global",
        command_names=sorted(command.name for command in synced),
    )


def registered_command_names(tree: discord.app_commands.CommandTree) -> list[str]:
    return sorted(command.name for command in tree.get_commands())


def format_command_names(command_names: list[str]) -> str:
    if not command_names:
        return "none"

    return ", ".join(f"/{name}" for name in command_names)
