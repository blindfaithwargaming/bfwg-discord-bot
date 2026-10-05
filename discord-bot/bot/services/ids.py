from __future__ import annotations

import re


_DISCORD_SNOWFLAKE_RE = re.compile(r"^[0-9]{17,20}$")


def validate_guild_id(guild_id: str) -> str:
    if not _DISCORD_SNOWFLAKE_RE.fullmatch(guild_id):
        raise ValueError("guild_id must be a Discord snowflake ID.")

    return guild_id
