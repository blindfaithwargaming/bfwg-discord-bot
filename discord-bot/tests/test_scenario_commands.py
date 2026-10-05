import asyncio
from types import SimpleNamespace

from bot.commands.scenario import RerollView


def _make_reroll_view(owner_id: int = 42) -> RerollView:
    return RerollView(
        owner_user_id=owner_id,
        attacker="Sons of Horus",
        defender="Imperial Fists",
        tone=None,
    )


def _make_non_owner_interaction(*, user_id: int = 99) -> tuple[SimpleNamespace, list[str]]:
    sent: list[str] = []

    async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(send_message=fake_send_message),
    )
    return interaction, sent


def test_reroll_view_stores_owner_user_id() -> None:
    view = _make_reroll_view(owner_id=99)
    assert view._owner_user_id == 99


def test_reroll_view_rejects_non_owner() -> None:
    view = _make_reroll_view(owner_id=42)
    interaction, sent = _make_non_owner_interaction(user_id=99)

    asyncio.run(view.reroll_button.callback(interaction))

    assert len(sent) == 1
    assert "only the user" in sent[0].lower()


def test_reroll_view_rejects_non_owner_does_not_increment_counter() -> None:
    view = _make_reroll_view(owner_id=42)
    interaction, _ = _make_non_owner_interaction(user_id=99)

    asyncio.run(view.reroll_button.callback(interaction))

    assert view._counter == 0


def test_reroll_view_rejection_is_ephemeral() -> None:
    view = _make_reroll_view(owner_id=42)
    ephemerals: list[bool] = []

    async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
        ephemerals.append(ephemeral)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=99),
        response=SimpleNamespace(send_message=fake_send_message),
    )

    asyncio.run(view.reroll_button.callback(interaction))

    assert ephemerals == [True]


def test_reroll_view_allows_owner_and_increments_counter() -> None:
    view = _make_reroll_view(owner_id=42)
    rejection_sent: list[str] = []

    async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
        rejection_sent.append(msg)

    async def fake_edit_message(**kwargs: object) -> None:
        pass

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        response=SimpleNamespace(
            send_message=fake_send_message,
            edit_message=fake_edit_message,
        ),
    )

    asyncio.run(view.reroll_button.callback(interaction))

    assert view._counter == 1
    assert not any("only the user" in msg.lower() for msg in rejection_sent)


def test_reroll_view_multiple_non_owners_each_rejected() -> None:
    view = _make_reroll_view(owner_id=42)
    sent_by_user: dict[int, list[str]] = {10: [], 20: [], 30: []}

    async def run_for(user_id: int) -> None:
        captured: list[str] = []

        async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
            captured.append(msg)

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            response=SimpleNamespace(send_message=fake_send_message),
        )
        await view.reroll_button.callback(interaction)
        sent_by_user[user_id] = captured

    async def _run() -> None:
        await run_for(10)
        await run_for(20)
        await run_for(30)

    asyncio.run(_run())

    for user_id in (10, 20, 30):
        assert len(sent_by_user[user_id]) == 1
        assert "only the user" in sent_by_user[user_id][0].lower()

    assert view._counter == 0
