import asyncio
import logging
from types import SimpleNamespace

import pytest

from bot.commands import interaction_helpers
from bot.commands.interaction_helpers import (
    _UserRateLimiter,
    _is_publish_permitted,
    check_rate_limit,
    command_context,
    is_admin_or_owner,
    require_admin_or_owner,
    require_publish_permission,
)


def _make_interaction(
    guild_id: int | None = 111,
    channel_id: int | None = 222,
    user_id: int | None = 333,
    interaction_id: int = 444,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=interaction_id,
        guild_id=guild_id,
        channel_id=channel_id,
        user=SimpleNamespace(id=user_id),
    )


def _make_interaction_with_response(user_id: int = 100) -> tuple[SimpleNamespace, list[str]]:
    """Return an interaction mock and a list that collects ephemeral rejection messages."""
    sent: list[str] = []

    async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)

    async def fake_followup_send(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)

    interaction = SimpleNamespace(
        id=999,
        guild_id=111,
        channel_id=222,
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            is_done=lambda: False,
            send_message=fake_send_message,
        ),
        followup=SimpleNamespace(send=fake_followup_send),
    )
    return interaction, sent


def test_command_context_logs_invocation(caplog) -> None:
    interaction = _make_interaction(guild_id=111, channel_id=222, user_id=333)

    async def _run():
        async with command_context(interaction, "lore random"):
            pass

    with caplog.at_level(logging.INFO):
        asyncio.run(_run())

    invocation_records = [r for r in caplog.records if "invoked" in r.message]
    assert len(invocation_records) == 1
    msg = invocation_records[0].message
    assert "lore random" in msg
    assert "guild=111" in msg
    assert "channel=222" in msg
    assert "user=333" in msg


def test_command_context_logs_completion_with_elapsed(caplog) -> None:
    interaction = _make_interaction()

    async def _run():
        async with command_context(interaction, "ping"):
            pass

    with caplog.at_level(logging.INFO):
        asyncio.run(_run())

    done_records = [r for r in caplog.records if "done" in r.message]
    assert len(done_records) == 1
    assert "elapsed_ms=" in done_records[0].message


def test_command_context_logs_done_on_early_return(caplog) -> None:
    interaction = _make_interaction()

    async def _cmd() -> None:
        async with command_context(interaction, "stadium-event"):
            return  # early exit

    with caplog.at_level(logging.INFO):
        asyncio.run(_cmd())

    done_records = [r for r in caplog.records if "done" in r.message]
    assert len(done_records) == 1


def test_command_context_logs_done_even_on_exception(caplog) -> None:
    interaction = _make_interaction()

    async def _run():
        async with command_context(interaction, "scenario seed"):
            raise ValueError("boom")

    with caplog.at_level(logging.INFO):
        with pytest.raises(ValueError):
            asyncio.run(_run())

    done_records = [r for r in caplog.records if "done" in r.message]
    assert len(done_records) == 1


def test_command_context_handles_none_guild_and_channel(caplog) -> None:
    interaction = _make_interaction(guild_id=None, channel_id=None, user_id=999)

    async def _run():
        async with command_context(interaction, "ping"):
            pass

    with caplog.at_level(logging.INFO):
        asyncio.run(_run())

    invocation_records = [r for r in caplog.records if "invoked" in r.message]
    assert len(invocation_records) == 1
    assert "guild=None" in invocation_records[0].message
    assert "channel=None" in invocation_records[0].message


def test_command_context_runs_body_without_any_cloud_dependency() -> None:
    """command_context never blocks the command; there is no publish path to fail."""
    interaction = _make_interaction(interaction_id=555)
    ran = False

    async def _run():
        nonlocal ran
        async with command_context(interaction, "ping"):
            ran = True

    asyncio.run(_run())
    assert ran is True


# --- _UserRateLimiter unit tests ---


def test_rate_limiter_allows_first_invocation() -> None:
    limiter = _UserRateLimiter(cooldown_seconds=3.0)
    assert limiter.acquire(user_id=1) == 0.0


def test_rate_limiter_blocks_immediate_second_invocation() -> None:
    limiter = _UserRateLimiter(cooldown_seconds=3.0)
    limiter.acquire(user_id=1)
    remaining = limiter.acquire(user_id=1)
    assert remaining > 0.0
    assert remaining <= 3.0


def test_rate_limiter_isolates_users() -> None:
    limiter = _UserRateLimiter(cooldown_seconds=3.0)
    limiter.acquire(user_id=1)
    assert limiter.acquire(user_id=2) == 0.0


def test_rate_limiter_does_not_update_timestamp_on_rejection() -> None:
    limiter = _UserRateLimiter(cooldown_seconds=3.0)
    limiter.acquire(user_id=1)
    first_remaining = limiter.acquire(user_id=1)   # rate-limited
    second_remaining = limiter.acquire(user_id=1)  # should be same or smaller
    assert first_remaining > 0.0
    assert second_remaining <= first_remaining


def test_rate_limiter_reset_clears_all_state() -> None:
    limiter = _UserRateLimiter(cooldown_seconds=3.0)
    limiter.acquire(user_id=1)
    limiter.reset()
    assert limiter.acquire(user_id=1) == 0.0


# --- check_rate_limit integration tests ---


def test_check_rate_limit_allows_first_invocation() -> None:
    interaction, sent = _make_interaction_with_response(user_id=10)

    async def _run() -> bool:
        return await check_rate_limit(interaction, "lore random")

    result = asyncio.run(_run())
    assert result is True
    assert sent == []


def test_check_rate_limit_blocks_second_invocation_and_sends_message() -> None:
    interaction, sent = _make_interaction_with_response(user_id=20)

    async def _run() -> tuple[bool, bool]:
        first = await check_rate_limit(interaction, "lore search")
        second = await check_rate_limit(interaction, "lore search")
        return first, second

    first, second = asyncio.run(_run())
    assert first is True
    assert second is False
    assert len(sent) == 1
    assert "Please wait" in sent[0]


def test_check_rate_limit_isolates_users() -> None:
    interaction_a, sent_a = _make_interaction_with_response(user_id=30)
    interaction_b, sent_b = _make_interaction_with_response(user_id=31)

    async def _run() -> tuple[bool, bool]:
        await check_rate_limit(interaction_a, "source")
        second_a = await check_rate_limit(interaction_a, "source")
        second_b = await check_rate_limit(interaction_b, "source")
        return second_a, second_b

    second_a, second_b = asyncio.run(_run())
    assert second_a is False
    assert second_b is True


def test_check_rate_limit_uses_followup_when_response_done() -> None:
    sent: list[str] = []

    async def fake_followup_send(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=40),
        response=SimpleNamespace(
            is_done=lambda: True,
            send_message=None,
        ),
        followup=SimpleNamespace(send=fake_followup_send),
    )

    async def _run() -> bool:
        await check_rate_limit(interaction, "scenario seed")  # first — sets timestamp
        return await check_rate_limit(interaction, "scenario seed")  # second — rate limited

    result = asyncio.run(_run())
    assert result is False
    assert len(sent) == 1
    assert "Please wait" in sent[0]


def test_check_rate_limit_allows_user_with_no_id() -> None:
    interaction = SimpleNamespace(user=SimpleNamespace(id=None))

    async def _run() -> bool:
        return await check_rate_limit(interaction, "lore random")

    assert asyncio.run(_run()) is True


def test_check_rate_limit_logs_rate_limited_invocation(caplog) -> None:
    interaction, _ = _make_interaction_with_response(user_id=50)

    async def _run() -> None:
        await check_rate_limit(interaction, "source")
        await check_rate_limit(interaction, "source")

    with caplog.at_level(logging.INFO):
        asyncio.run(_run())

    rate_limited_records = [r for r in caplog.records if "rate-limited" in r.message]
    assert len(rate_limited_records) == 1
    assert "user=50" in rate_limited_records[0].message


# --- _is_publish_permitted unit tests ---


def test_is_publish_permitted_allows_bot_owner(monkeypatch) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset({123}))
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123, guild_permissions=SimpleNamespace(administrator=False)),
        permissions=SimpleNamespace(manage_threads=False),
    )
    assert _is_publish_permitted(interaction) is True


def test_is_publish_permitted_allows_discord_administrator(monkeypatch) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset())
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=456, guild_permissions=SimpleNamespace(administrator=True)),
        permissions=SimpleNamespace(manage_threads=False),
    )
    assert _is_publish_permitted(interaction) is True


def test_is_publish_permitted_allows_manage_threads(monkeypatch) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset())
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=789, guild_permissions=SimpleNamespace(administrator=False)),
        permissions=SimpleNamespace(manage_threads=True),
    )
    assert _is_publish_permitted(interaction) is True


def test_is_publish_permitted_rejects_unprivileged_user(monkeypatch) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset({999}))
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123, guild_permissions=SimpleNamespace(administrator=False)),
        permissions=SimpleNamespace(manage_threads=False),
    )
    assert _is_publish_permitted(interaction) is False


def test_is_publish_permitted_handles_missing_permissions_attributes(monkeypatch) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset())
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1, guild_permissions=None),
        permissions=None,
    )
    assert _is_publish_permitted(interaction) is False


# --- require_admin_or_owner integration tests ---


def test_is_admin_or_owner_allows_discord_administrator(monkeypatch) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset())
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=456, guild_permissions=SimpleNamespace(administrator=True)),
    )

    assert is_admin_or_owner(interaction) is True


def test_require_admin_or_owner_rejects_manage_threads_without_admin(monkeypatch) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset())
    )
    sent: list[str] = []

    async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123, guild_permissions=SimpleNamespace(administrator=False)),
        permissions=SimpleNamespace(manage_threads=True),
        response=SimpleNamespace(is_done=lambda: False, send_message=fake_send_message),
    )

    result = asyncio.run(require_admin_or_owner(interaction, "club-night next"))
    assert result is False
    assert len(sent) == 1
    assert "administrator" in sent[0].lower()
    assert "manage threads" not in sent[0].lower()


# --- require_publish_permission integration tests ---


def test_require_publish_permission_allows_permitted_user(monkeypatch) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset({123}))
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123, guild_permissions=SimpleNamespace(administrator=False)),
        permissions=SimpleNamespace(manage_threads=False),
        response=SimpleNamespace(is_done=lambda: False),
    )

    result = asyncio.run(require_publish_permission(interaction))
    assert result is True


def test_require_publish_permission_rejects_and_sends_message(monkeypatch) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset({999}))
    )
    sent: list[str] = []

    async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123, guild_permissions=SimpleNamespace(administrator=False)),
        permissions=SimpleNamespace(manage_threads=False),
        response=SimpleNamespace(is_done=lambda: False, send_message=fake_send_message),
    )

    result = asyncio.run(require_publish_permission(interaction))
    assert result is False
    assert len(sent) == 1
    assert "manage threads" in sent[0].lower()


def test_require_publish_permission_uses_followup_when_response_done(monkeypatch) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset())
    )
    sent: list[str] = []

    async def fake_followup_send(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123, guild_permissions=SimpleNamespace(administrator=False)),
        permissions=SimpleNamespace(manage_threads=False),
        response=SimpleNamespace(is_done=lambda: True),
        followup=SimpleNamespace(send=fake_followup_send),
    )

    result = asyncio.run(require_publish_permission(interaction))
    assert result is False
    assert len(sent) == 1


def test_require_publish_permission_logs_rejection(monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset())
    )
    sent: list[str] = []

    async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=77, guild_permissions=SimpleNamespace(administrator=False)),
        permissions=SimpleNamespace(manage_threads=False),
        response=SimpleNamespace(is_done=lambda: False, send_message=fake_send_message),
    )

    with caplog.at_level(logging.WARNING):
        asyncio.run(require_publish_permission(interaction))

    warning_records = [r for r in caplog.records if "publish" in r.message.lower()]
    assert len(warning_records) == 1
    assert "77" in warning_records[0].message
