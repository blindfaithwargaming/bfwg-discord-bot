import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import discord

from bot.config import settings


INTERACTION_ALREADY_ACKNOWLEDGED = 40060
_RATE_LIMIT_COOLDOWN_SECONDS = 3.0


class _UserRateLimiter:
    """Per-user rate limiter backed by an asyncio-safe in-memory dict."""

    def __init__(self, cooldown_seconds: float = _RATE_LIMIT_COOLDOWN_SECONDS) -> None:
        self._cooldown = cooldown_seconds
        self._last: dict[int, float] = {}

    def acquire(self, user_id: int) -> float:
        """Return remaining cooldown in seconds.

        If 0.0, the user is allowed and the timestamp is updated for next check.
        If > 0.0, the user is rate-limited and the timestamp is NOT updated.
        """
        now = time.monotonic()
        last = self._last.get(user_id, 0.0)
        remaining = max(0.0, self._cooldown - (now - last))

        if remaining == 0.0:
            self._last[user_id] = now

        return remaining

    def reset(self) -> None:
        self._last.clear()


_command_rate_limiter = _UserRateLimiter()


@asynccontextmanager
async def command_context(
    interaction: discord.Interaction,
    command_name: str,
) -> AsyncIterator[None]:
    guild_id = interaction.guild_id
    channel_id = interaction.channel_id
    user_id = getattr(interaction.user, "id", None)
    start = time.monotonic()

    logging.info(
        "/%s invoked guild=%s channel=%s user=%s",
        command_name,
        guild_id,
        channel_id,
        user_id,
    )

    success = True
    try:
        yield
    except Exception:
        success = False
        raise
    finally:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logging.info(
            "/%s done guild=%s channel=%s user=%s success=%s elapsed_ms=%d",
            command_name,
            guild_id,
            channel_id,
            user_id,
            success,
            elapsed_ms,
        )


async def check_rate_limit(
    interaction: discord.Interaction,
    command_name: str,
) -> bool:
    """Return True if the user may proceed; False if they are rate-limited.

    When rate-limited, sends an ephemeral rejection to the interaction and
    returns False so the caller can bail out immediately.
    """
    user_id = getattr(interaction.user, "id", None)

    if user_id is None:
        return True

    remaining = _command_rate_limiter.acquire(user_id)

    if remaining == 0.0:
        return True

    logging.info(
        "/%s rate-limited user=%s remaining=%.1fs",
        command_name,
        user_id,
        remaining,
    )
    message = f"Please wait {remaining:.1f}s before using this command again."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

    return False


async def defer_ephemeral(
    interaction: discord.Interaction,
    command_name: str,
    ephemeral: bool = True,
) -> bool:
    if interaction.response.is_done():
        logging.info("Discord interaction for /%s was already acknowledged.", command_name)
        return True

    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        return True
    except discord.NotFound:
        logging.warning("Discord interaction expired before /%s could be acknowledged.", command_name)
        return False
    except discord.HTTPException as exc:
        if exc.code == INTERACTION_ALREADY_ACKNOWLEDGED:
            logging.info("Discord interaction for /%s was already acknowledged.", command_name)
            return True

        raise


def _is_publish_permitted(interaction: discord.Interaction) -> bool:
    if is_admin_or_owner(interaction):
        return True

    channel_perms = getattr(interaction, "permissions", None)
    return bool(getattr(channel_perms, "manage_threads", False))


def is_admin_or_owner(interaction: discord.Interaction) -> bool:
    user_id = getattr(interaction.user, "id", None)

    if user_id in settings.bot_owner_user_ids:
        return True

    guild_perms = getattr(interaction.user, "guild_permissions", None)
    return bool(getattr(guild_perms, "administrator", False))


async def require_admin_or_owner(
    interaction: discord.Interaction,
    command_name: str,
) -> bool:
    """Return True if the user may invoke an admin-only command."""
    if is_admin_or_owner(interaction):
        return True

    logging.warning(
        "Rejected /%s from user_id=%s without runtime admin or owner permission.",
        command_name,
        getattr(interaction.user, "id", "unknown"),
    )
    message = "You need Discord administrator permission or bot owner access to use this command."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

    return False


async def require_publish_permission(interaction: discord.Interaction) -> bool:
    """Return True if the user may invoke a publish-like command; False otherwise.

    Allowed if the user is a configured bot owner, a Discord administrator, or
    has Manage Threads permission in the invoked channel.  When denied, sends an
    ephemeral rejection and returns False so the caller can bail out immediately.
    """
    if _is_publish_permitted(interaction):
        return True

    logging.warning(
        "Rejected publish command from user_id=%s without publish permission.",
        getattr(interaction.user, "id", "unknown"),
    )
    message = (
        "You need Discord administrator permission, Manage Threads, "
        "or bot owner access to use this command."
    )

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

    return False
