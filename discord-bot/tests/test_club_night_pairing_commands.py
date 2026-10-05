import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.commands import club_night_pairings as pairing_commands
from bot.commands import interaction_helpers
from bot.services.club_night_pairing_history import PairingHistoryStore
from bot.services.club_night_signups import SignupStore
from bot.services.stadium_events import ClubNight


pytestmark = pytest.mark.usefixtures("club_night_clock")

GUILD_ID = 123456789012345678
EVENT_DATE = date(2026, 6, 25)
CLUB_NIGHT = ClubNight(
    title="Organised Play - 25th June", event_date=EVENT_DATE, thread_id="555"
)


class FakeInteraction:
    event = CLUB_NIGHT
    def __init__(
        self,
        *,
        user_id: int = 11,
        display_name: str = "Aaron",
        guild_id: int | None = GUILD_ID,
        administrator: bool = False,
    ) -> None:
        self.user = SimpleNamespace(
            id=user_id,
            display_name=display_name,
            guild_permissions=SimpleNamespace(administrator=administrator),
        )
        self.guild_id = guild_id
        self.channel_id = 555
        self.client = SimpleNamespace()
        self.permissions = SimpleNamespace(manage_threads=False)
        self.followups: list[tuple[str, bool, object]] = []
        self.channel_sends: list[tuple[str, object]] = []
        self.deferred = False

        async def _defer(*, ephemeral: bool = True, thinking: bool = True) -> None:
            self.deferred = True

        self.response = SimpleNamespace(is_done=lambda: False, defer=_defer)

        async def _followup_send(
            message: str,
            *,
            ephemeral: bool = False,
            allowed_mentions: object = None,
        ) -> None:
            self.followups.append((message, ephemeral, allowed_mentions))

        self.followup = SimpleNamespace(send=_followup_send)

        async def _channel_send(message: str, *, allowed_mentions: object = None) -> None:
            self.channel_sends.append((message, allowed_mentions))

        self.channel = SimpleNamespace(
            send=_channel_send,
            id=555,
            parent_id=999,
            name=self.event.title if self.event else "Other discussion",
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
        )


def _patch_environment(
    monkeypatch,
    tmp_path: Path,
    *,
    club_night: ClubNight | None = CLUB_NIGHT,
    lookup_error: bool = False,
) -> Path:
    state_path = tmp_path / "state" / "club_night_signups.json"
    history_path = tmp_path / "state" / "club_night_pairing_history.json"
    constraints_path = tmp_path / "state" / "club_night_pairing_constraints.json"
    monkeypatch.setattr(
        pairing_commands,
        "settings",
        SimpleNamespace(
            club_night_forum_channel_id=999,
            club_night_signup_state_path=str(state_path),
            club_night_pairing_history_state_path=str(history_path),
            club_night_pairing_constraints_state_path=str(constraints_path),
            club_night_pairing_lookback=2,
        ),
    )

    monkeypatch.setattr(FakeInteraction, "event", None if lookup_error else club_night)
    return state_path


def _store(state_path: Path) -> SignupStore:
    return SignupStore(state_path, event=CLUB_NIGHT)


def _reset_rate_limiter() -> None:
    interaction_helpers._command_rate_limiter.reset()


def _run_signup(
    interaction: FakeInteraction,
    side: str | None = None,
    format_pref: str | None = None,
) -> None:
    side_choice = SimpleNamespace(value=side) if side is not None else None
    format_choice = (
        SimpleNamespace(value=format_pref) if format_pref is not None else None
    )
    asyncio.run(
        pairing_commands.club_night_signup.callback(
            interaction, side_choice, format_choice
        )
    )


def test_signup_records_player_and_confirms(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_signup(interaction)

    signups = _store(state_path).get_signups(str(GUILD_ID), EVENT_DATE)
    assert set(signups) == {"11"}
    assert signups["11"]["display_name"] == "Aaron"
    assert signups["11"]["side"] == "either"

    assert len(interaction.followups) == 1
    message, ephemeral, allowed = interaction.followups[0]
    assert "Signed up for Organised Play - 25th June" in message
    assert "1 attending" in message
    assert ephemeral is True
    assert allowed is pairing_commands.NO_MENTIONS


def test_signup_records_chosen_side(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_signup(interaction, side="loyalist")

    signups = _store(state_path).get_signups(str(GUILD_ID), EVENT_DATE)
    assert signups["11"]["side"] == "loyalist"
    assert "as Loyalist" in interaction.followups[0][0]


def test_signup_records_chosen_format(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_signup(interaction, format_pref="doubles")

    signups = _store(state_path).get_signups(str(GUILD_ID), EVENT_DATE)
    assert signups["11"]["format_pref"] == "doubles"
    assert "doubles" in interaction.followups[0][0].lower()


def test_signup_default_nudges_towards_doubles(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_signup(interaction)

    assert "format:Doubles" in interaction.followups[0][0]


def test_signup_without_club_night_reports_ephemerally(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path, club_night=None)
    interaction = FakeInteraction()

    _run_signup(interaction)

    assert interaction.followups == [
        (pairing_commands.NO_CLUB_NIGHT_MESSAGE, True, pairing_commands.NO_MENTIONS)
    ]


def test_signup_unrecognised_thread_reports_ephemerally(
    monkeypatch, tmp_path: Path
) -> None:
    _patch_environment(monkeypatch, tmp_path, lookup_error=True)
    interaction = FakeInteraction()

    _run_signup(interaction)

    message, ephemeral, _ = interaction.followups[0]
    assert "organised play forum" in message
    assert ephemeral is True


def test_signup_outside_guild_rejected(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(guild_id=None)

    _run_signup(interaction)

    assert interaction.followups == [
        (pairing_commands.GUILD_ONLY_MESSAGE, True, pairing_commands.NO_MENTIONS)
    ]


def test_signup_store_oserror_reports_ephemerally(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    state_path.parent.parent.mkdir(parents=True, exist_ok=True)
    # A file where the state directory should be makes mkdir raise OSError.
    state_path.parent.write_text("blocker", encoding="utf-8")
    interaction = FakeInteraction()

    _run_signup(interaction)

    message, ephemeral, _ = interaction.followups[0]
    assert message == pairing_commands.STORE_ERROR_MESSAGE
    assert ephemeral is True


def test_withdraw_removes_signup(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    _run_signup(interaction)
    _reset_rate_limiter()

    asyncio.run(pairing_commands.club_night_withdraw.callback(interaction))

    assert _store(state_path).get_signups(str(GUILD_ID), EVENT_DATE) == {}
    assert "no longer signed up" in interaction.followups[-1][0]


def test_withdraw_when_not_signed_up(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    asyncio.run(pairing_commands.club_night_withdraw.callback(interaction))

    assert "weren't signed up" in interaction.followups[0][0]


def test_signups_lists_players_with_sides(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave", side="traitor")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "33", "Cei")
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_signups.callback(interaction))

    message, ephemeral, _ = interaction.followups[0]
    assert "3 players signed up" in message
    assert "- Aaron (Horus Heresy, Loyalist)" in message
    assert "- Dave (Horus Heresy, Traitor)" in message
    assert "- Cei (Horus Heresy)" in message
    assert ephemeral is True


def test_signups_empty_list(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_signups.callback(interaction))

    assert "No one has signed up" in interaction.followups[0][0]


def test_signups_store_read_error_reports_ephemerally(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    state_path.parent.mkdir(parents=True)
    state_path.mkdir()
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_signups.callback(interaction))

    message, ephemeral, allowed = interaction.followups[0]
    assert message == pairing_commands.STORE_ERROR_MESSAGE
    assert ephemeral is True
    assert allowed is pairing_commands.NO_MENTIONS


def test_signups_escapes_display_names(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "@everyone")
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_signups.callback(interaction))

    assert "@everyone" not in interaction.followups[0][0]


def test_signups_rejects_non_admin(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(administrator=False)

    sent: list[tuple[str, bool]] = []

    async def fake_send_message(message: str, *, ephemeral: bool = False) -> None:
        sent.append((message, ephemeral))

    interaction.response.send_message = fake_send_message

    asyncio.run(pairing_commands.club_night_signups.callback(interaction))

    assert len(sent) == 1
    assert "administrator" in sent[0][0].lower()
    assert sent[0][1] is True
    assert interaction.followups == []


def test_prefer_requires_signup(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    player = SimpleNamespace(id=22, display_name="Dave", bot=False)

    asyncio.run(pairing_commands.club_night_prefer.callback(interaction, player, False))

    assert interaction.followups[0][0] == pairing_commands.NOT_SIGNED_UP_MESSAGE


def test_prefer_requires_player_unless_clearing(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    _store(state_path).add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron")
    interaction = FakeInteraction()

    asyncio.run(pairing_commands.club_night_prefer.callback(interaction, None, False))

    assert "Choose a player" in interaction.followups[0][0]


def test_prefer_rejects_self(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    _store(state_path).add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron")
    interaction = FakeInteraction()
    player = SimpleNamespace(id=11, display_name="Aaron", bot=False)

    asyncio.run(pairing_commands.club_night_prefer.callback(interaction, player, False))

    assert "yourself" in interaction.followups[0][0]


def test_prefer_rejects_bot(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    _store(state_path).add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron")
    interaction = FakeInteraction()
    player = SimpleNamespace(id=22, display_name="BFWG Bot", bot=True)

    asyncio.run(pairing_commands.club_night_prefer.callback(interaction, player, False))

    assert "bot" in interaction.followups[0][0].lower()


def test_prefer_stores_preference(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave")
    interaction = FakeInteraction()
    player = SimpleNamespace(id=22, display_name="Dave", bot=False)

    asyncio.run(pairing_commands.club_night_prefer.callback(interaction, player, False))

    signups = store.get_signups(str(GUILD_ID), EVENT_DATE)
    assert signups["11"]["preferred_user_id"] == "22"
    message = interaction.followups[0][0]
    assert "Dave" in message
    assert "prefer you back" in message
    assert "haven't signed up" not in message


def test_prefer_notes_unsigned_target(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    _store(state_path).add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron")
    interaction = FakeInteraction()
    player = SimpleNamespace(id=22, display_name="Dave", bot=False)

    asyncio.run(pairing_commands.club_night_prefer.callback(interaction, player, False))

    assert "haven't signed up yet" in interaction.followups[0][0]


def test_prefer_clear(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron")
    store.set_preference(str(GUILD_ID), EVENT_DATE, "11", "22")
    interaction = FakeInteraction()

    asyncio.run(pairing_commands.club_night_prefer.callback(interaction, None, True))

    assert store.get_signups(str(GUILD_ID), EVENT_DATE)["11"]["preferred_user_id"] is None
    assert "cleared" in interaction.followups[0][0]


def test_prefer_store_read_error_reports_ephemerally(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    state_path.parent.mkdir(parents=True)
    state_path.mkdir()
    interaction = FakeInteraction()
    player = SimpleNamespace(id=22, display_name="Dave", bot=False)

    asyncio.run(pairing_commands.club_night_prefer.callback(interaction, player, False))

    message, ephemeral, allowed = interaction.followups[0]
    assert message == pairing_commands.STORE_ERROR_MESSAGE
    assert ephemeral is True
    assert allowed is pairing_commands.NO_MENTIONS


def test_pairings_rejects_non_organiser(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(administrator=False)

    sent: list[tuple[str, bool]] = []

    async def fake_send_message(message: str, *, ephemeral: bool = False) -> None:
        sent.append((message, ephemeral))

    interaction.response.send_message = fake_send_message

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    assert len(sent) == 1
    assert "administrator" in sent[0][0].lower()
    assert sent[0][1] is True
    assert interaction.channel_sends == []


def test_pairings_too_few_signups(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    _store(state_path).add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron")
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    assert "Only 1 player signed up" in interaction.followups[0][0]
    assert interaction.channel_sends == []


def test_pairings_store_read_error_reports_ephemerally(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    state_path.parent.mkdir(parents=True)
    state_path.mkdir()
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    message, ephemeral, allowed = interaction.followups[0]
    assert message == pairing_commands.STORE_ERROR_MESSAGE
    assert ephemeral is True
    assert allowed is pairing_commands.NO_MENTIONS
    assert interaction.channel_sends == []


def test_pairings_constraints_read_error_blocks_post(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave", side="traitor")
    # A directory at the constraints path makes the authoritative read raise.
    (tmp_path / "state" / "club_night_pairing_constraints.json").mkdir()
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    assert interaction.channel_sends == []
    message, ephemeral, allowed = interaction.followups[-1]
    assert message == pairing_commands.CONSTRAINTS_ERROR_MESSAGE
    assert ephemeral is True
    assert allowed is pairing_commands.NO_MENTIONS


def test_pairings_corrupt_constraints_block_post(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave", side="traitor")
    constraints_path = tmp_path / "state" / "club_night_pairing_constraints.json"
    constraints_path.write_text("not json{", encoding="utf-8")
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    assert interaction.channel_sends == []
    message, ephemeral, allowed = interaction.followups[-1]
    assert message == pairing_commands.CONSTRAINTS_ERROR_MESSAGE
    assert ephemeral is True
    assert allowed is pairing_commands.NO_MENTIONS


def test_pairings_posts_publicly_with_no_mentions(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave", side="traitor")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "33", "Cei")
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    assert len(interaction.channel_sends) == 1
    text, allowed = interaction.channel_sends[0]
    assert allowed is pairing_commands.NO_MENTIONS
    assert "Club night pairings" in text
    for name in ("Aaron", "Dave", "Cei"):
        assert name in text

    assert interaction.followups[-1][0] == "Pairings posted."
    assert interaction.followups[-1][1] is True


def _history(tmp_path: Path) -> PairingHistoryStore:
    return PairingHistoryStore(tmp_path / "state" / "club_night_pairing_history.json")


def _recorded_pairs(tmp_path: Path) -> list[list[str]]:
    data = json.loads(
        (tmp_path / "state" / "club_night_pairing_history.json").read_text(
            encoding="utf-8"
        )
    )
    return data[str(GUILD_ID)][EVENT_DATE.isoformat() + "|heresy|555"]["pairs"]


def test_pairings_records_posted_pairs_to_history(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave", side="traitor")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "33", "Cei", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "44", "Emrys", side="traitor")
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    recorded = _recorded_pairs(tmp_path)
    flattened = sorted(player for pair in recorded for player in pair)
    assert flattened == ["11", "22", "33", "44"]
    assert all(pair == sorted(pair) for pair in recorded)
    assert interaction.followups[-1][0] == pairing_commands.PAIRINGS_POSTED_MESSAGE


def test_pairings_records_opponent_pairs_not_teammates_for_doubles(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    # Two keen loyalists team up against one traitor -> a 2v1.
    store.add_signup(
        str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist", format_pref="doubles"
    )
    store.add_signup(
        str(GUILD_ID), EVENT_DATE, "22", "Bran", side="loyalist", format_pref="doubles"
    )
    store.add_signup(str(GUILD_ID), EVENT_DATE, "33", "Cei", side="traitor")
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    recorded = {frozenset(pair) for pair in _recorded_pairs(tmp_path)}
    # The two teammates are not opponents, so their pair is never recorded.
    assert frozenset({"11", "22"}) not in recorded
    assert recorded == {frozenset({"11", "33"}), frozenset({"22", "33"})}
    text = interaction.channel_sends[0][0]
    assert (
        "Aaron (Horus Heresy) (Loyalist) & Bran (Horus Heresy) (Loyalist) vs Cei (Horus Heresy) (Traitor)"
        in text
    )


def test_pairings_record_overwrites_previous_for_same_date(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave", side="traitor")
    _history(tmp_path).record_pairings(str(GUILD_ID), EVENT_DATE, [("98", "99")])
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    recorded = _recorded_pairs(tmp_path)
    assert recorded == [["11", "22"]]


def test_pairings_avoids_recent_opponent_from_prior_date(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave", side="traitor")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "33", "Cei", side="traitor")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "44", "Emrys", side="traitor")
    _history(tmp_path).record_pairings(
        str(GUILD_ID), date(2026, 6, 11), [("11", "22")]
    )
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    recorded = _recorded_pairs(tmp_path)
    assert ["11", "22"] not in recorded


def test_pairings_history_read_failure_still_posts(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave", side="traitor")
    # A directory at the history path makes both read and write raise OSError.
    (tmp_path / "state" / "club_night_pairing_history.json").mkdir()
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    assert len(interaction.channel_sends) == 1
    assert "Club night pairings" in interaction.channel_sends[0][0]


def test_pairings_history_write_failure_warns_but_posts(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave", side="traitor")
    # Point history at a path whose parent is a file, so reads return empty but
    # writes raise OSError when the store tries to create the parent directory.
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    pairing_commands.settings.club_night_pairing_history_state_path = str(
        blocker / "history.json"
    )
    interaction = FakeInteraction(administrator=True)

    asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    assert len(interaction.channel_sends) == 1
    assert interaction.followups[-1][0] == pairing_commands.PAIRINGS_HISTORY_WARNING


def test_pairings_no_history_written_when_post_fails(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    store = _store(state_path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="loyalist")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Dave", side="traitor")
    interaction = FakeInteraction(administrator=True)

    async def _failing_send(message: str, *, allowed_mentions: object = None) -> None:
        raise RuntimeError("channel send failed")

    interaction.channel.send = _failing_send

    with pytest.raises(RuntimeError, match="channel send failed"):
        asyncio.run(pairing_commands.club_night_pairings.callback(interaction))

    assert not (tmp_path / "state" / "club_night_pairing_history.json").exists()


def test_my_signup_member_private_read_only(monkeypatch, tmp_path):
    path = _patch_environment(monkeypatch, tmp_path)
    store = _store(path)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron", side="traitor", format_pref="doubles")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "22", "Other player's private name")
    store.set_preference(str(GUILD_ID), EVENT_DATE, "11", "22")
    before = path.read_bytes()
    interaction = FakeInteraction()
    asyncio.run(pairing_commands.club_night_my_signup.callback(interaction))
    message, ephemeral, allowed = interaction.followups[0]
    assert "You're signed up" in message
    assert "Side: Traitor" in message
    assert "Format: Doubles" in message
    assert "Preferred opponent: <@22>" in message
    assert "Other player's private name" not in message
    assert ephemeral and interaction.deferred
    assert allowed is pairing_commands.NO_MENTIONS
    assert not interaction.channel_sends
    assert path.read_bytes() == before


def test_my_signup_missing_does_not_create_state(monkeypatch, tmp_path):
    path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    asyncio.run(pairing_commands.club_night_my_signup.callback(interaction))
    message, ephemeral, allowed = interaction.followups[0]
    assert "not signed up in this event thread" in message
    assert "/club-night signup" in message
    assert ephemeral and allowed is pairing_commands.NO_MENTIONS
    assert not path.exists()


def test_my_signup_read_error(monkeypatch, tmp_path):
    path = _patch_environment(monkeypatch, tmp_path)
    path.mkdir(parents=True)
    interaction = FakeInteraction()
    asyncio.run(pairing_commands.club_night_my_signup.callback(interaction))
    message, ephemeral, allowed = interaction.followups[0]
    assert "Could not read" in message
    assert ephemeral and allowed is pairing_commands.NO_MENTIONS


@pytest.mark.parametrize("guild_id,valid_thread", [(None, True), (GUILD_ID, False)])
def test_my_signup_requires_event_thread_in_guild(monkeypatch, tmp_path, guild_id, valid_thread):
    _patch_environment(monkeypatch, tmp_path, club_night=CLUB_NIGHT if valid_thread else None)
    interaction = FakeInteraction(guild_id=guild_id)
    asyncio.run(pairing_commands.club_night_my_signup.callback(interaction))
    message, ephemeral, allowed = interaction.followups[0]
    assert message == (pairing_commands.GUILD_ONLY_MESSAGE if guild_id is None else pairing_commands.NO_CLUB_NIGHT_MESSAGE)
    assert ephemeral and allowed is pairing_commands.NO_MENTIONS


def test_my_signup_does_not_show_signup_from_other_thread(monkeypatch, tmp_path):
    path = _patch_environment(monkeypatch, tmp_path)
    other = ClubNight(title=CLUB_NIGHT.title, event_date=EVENT_DATE, thread_id="777")
    SignupStore(path, event=other).add_signup(str(GUILD_ID), EVENT_DATE, "11", "Aaron")
    before = path.read_bytes()
    interaction = FakeInteraction()
    asyncio.run(pairing_commands.club_night_my_signup.callback(interaction))
    assert "not signed up in this event thread" in interaction.followups[0][0]
    assert path.read_bytes() == before
