import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.commands import club_night_admin as admin
from bot.commands import club_night_pairings as pairing_commands
from bot.commands import interaction_helpers
from bot.services.club_night_pairing_constraints import PairingConstraintsStore
from bot.services.club_night_signups import SignupStore
from bot.services.stadium_events import ClubNight


pytestmark = pytest.mark.usefixtures("club_night_clock")

GUILD_ID = 123456789012345678
EVENT_DATE = date(2026, 6, 25)
CLUB_NIGHT = ClubNight(
    title="Organised Play - 25th June", event_date=EVENT_DATE, thread_id="555"
)


class FakeMember:
    def __init__(self, member_id: int, name: str, *, bot: bool = False) -> None:
        self.id = member_id
        self.display_name = name
        self.bot = bot


class FakeInteraction:
    event = CLUB_NIGHT
    def __init__(
        self,
        *,
        user_id: int = 11,
        display_name: str = "Aaron",
        guild_id: int | None = GUILD_ID,
        administrator: bool = True,
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
        self.messages: list[str] = []
        self.channel_sends: list[str] = []
        self.views: list[object] = []
        self._done = False

        def _is_done() -> bool:
            return self._done

        async def _defer(*, ephemeral: bool = True, thinking: bool = True) -> None:
            self._done = True

        async def _response_send(message: str, *, ephemeral: bool = False) -> None:
            self.messages.append(message)

        self.response = SimpleNamespace(
            is_done=_is_done,
            defer=_defer,
            send_message=_response_send,
        )

        async def _followup_send(
            message: str,
            *,
            ephemeral: bool = False,
            allowed_mentions: object = None,
            view: object = None,
            wait: bool = False,
        ) -> object:
            self.messages.append(message)
            if view is not None:
                self.views.append(view)
            return SimpleNamespace(edit=_noop_edit)

        self.followup = SimpleNamespace(send=_followup_send)

        async def _channel_send(message: str, *, allowed_mentions: object = None) -> None:
            self.channel_sends.append(message)

        self.channel = SimpleNamespace(
            send=_channel_send,
            id=555,
            parent_id=999,
            name=self.event.title if self.event else "Other discussion",
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
        )


async def _noop_edit(**kwargs: object) -> None:
    pass


class FakeComponentInteraction:
    def __init__(self, *, user_id: int = 11, channel: object | None = None) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.channel_id = 555
        self.channel = channel
        self.rejections: list[str] = []
        self.edits: list[dict] = []
        self.channel_sends: list[str] = []

        async def _send_message(message: str, *, ephemeral: bool = True, **kwargs) -> None:
            self.rejections.append(message)

        async def _edit_message(**kwargs: object) -> None:
            self.edits.append(dict(kwargs))

        self.response = SimpleNamespace(
            send_message=_send_message,
            edit_message=_edit_message,
        )


def _patch_environment(
    monkeypatch,
    tmp_path: Path,
    *,
    club_night: ClubNight | None = CLUB_NIGHT,
) -> SimpleNamespace:
    paths = SimpleNamespace(
        signup=tmp_path / "state" / "signups.json",
        history=tmp_path / "state" / "history.json",
        constraints=tmp_path / "state" / "constraints.json",
    )
    fake_settings = SimpleNamespace(
        club_night_forum_channel_id=999,
        club_night_signup_state_path=str(paths.signup),
        club_night_pairing_history_state_path=str(paths.history),
        club_night_pairing_constraints_state_path=str(paths.constraints),
        club_night_pairing_lookback=2,
        bot_owner_user_ids=frozenset(),
    )
    monkeypatch.setattr(pairing_commands, "settings", fake_settings)
    monkeypatch.setattr(admin, "settings", fake_settings)
    monkeypatch.setattr(interaction_helpers, "settings", fake_settings)

    monkeypatch.setattr(FakeInteraction, "event", club_night)
    return paths


def _signup_store(paths: SimpleNamespace) -> SignupStore:
    return SignupStore(paths.signup, event=CLUB_NIGHT)


def _constraints_store(paths: SimpleNamespace) -> PairingConstraintsStore:
    return PairingConstraintsStore(paths.constraints, event=CLUB_NIGHT)


def _reset_rate_limiter() -> None:
    interaction_helpers._command_rate_limiter.reset()


def setup_function() -> None:
    _reset_rate_limiter()


# --- add-player -------------------------------------------------------------


def test_add_player_persists_and_confirms(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    member = FakeMember(42, "Dave")

    asyncio.run(
        admin.club_night_add_player.callback(
            interaction, member, SimpleNamespace(value="loyalist")
        )
    )

    signups = _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE)
    assert signups["42"]["display_name"] == "Dave"
    assert signups["42"]["side"] == "loyalist"
    assert signups["42"]["format_pref"] == "solo"
    assert "Signed up Dave as Loyalist" in interaction.messages[-1]


def test_add_player_records_doubles_format(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    member = FakeMember(42, "Dave")

    asyncio.run(
        admin.club_night_add_player.callback(
            interaction,
            member,
            SimpleNamespace(value="loyalist"),
            SimpleNamespace(value="doubles"),
        )
    )

    signups = _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE)
    assert signups["42"]["format_pref"] == "doubles"


def test_add_player_without_format_preserves_existing_choice(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _signup_store(paths).add_signup(
        str(GUILD_ID), EVENT_DATE, "42", "Dave", format_pref="doubles"
    )
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_add_player.callback(
            interaction,
            FakeMember(42, "Dave"),
            SimpleNamespace(value="traitor"),
            None,
        )
    )

    signup = _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE)["42"]
    assert signup["side"] == "traitor"
    assert signup["format_pref"] == "doubles"


def test_add_player_rejects_non_admin(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(administrator=False)
    member = FakeMember(42, "Dave")

    asyncio.run(admin.club_night_add_player.callback(interaction, member, None))

    assert _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE) == {}
    assert "administrator" in interaction.messages[-1].lower()


def test_add_player_rejects_outside_guild(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(guild_id=None)
    member = FakeMember(42, "Dave")

    asyncio.run(admin.club_night_add_player.callback(interaction, member, None))

    assert interaction.messages[-1] == pairing_commands.GUILD_ONLY_MESSAGE


def test_add_player_rejects_bot(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    member = FakeMember(42, "BotBuddy", bot=True)

    asyncio.run(admin.club_night_add_player.callback(interaction, member, None))

    assert _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE) == {}
    assert "bot" in interaction.messages[-1].lower()


def test_add_player_escapes_display_name(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    member = FakeMember(42, "@everyone **boss**")

    asyncio.run(admin.club_night_add_player.callback(interaction, member, None))

    assert "@everyone" not in interaction.messages[-1]
    assert "**boss**" not in interaction.messages[-1]


def test_add_player_rate_limited_second_call(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    member = FakeMember(42, "Dave")

    asyncio.run(admin.club_night_add_player.callback(interaction, member, None))
    asyncio.run(admin.club_night_add_player.callback(interaction, member, None))

    # The second call is rate-limited before any second persist.
    assert "Please wait" in interaction.messages[-1]


# --- remove-player ----------------------------------------------------------


def test_remove_player_removes_signup(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _signup_store(paths).add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_remove_player.callback(interaction, FakeMember(42, "Dave"))
    )

    assert _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE) == {}
    assert "Removed Dave's sign-up" in interaction.messages[-1]


def test_remove_player_reports_when_absent(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_remove_player.callback(interaction, FakeMember(42, "Dave"))
    )

    assert "wasn't signed up" in interaction.messages[-1]


# --- set-side ---------------------------------------------------------------


def test_set_side_overrides(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _signup_store(paths).add_signup(
        str(GUILD_ID), EVENT_DATE, "42", "Dave", side="loyalist"
    )
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_set_side.callback(
            interaction, FakeMember(42, "Dave"), SimpleNamespace(value="traitor")
        )
    )

    assert _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE)["42"][
        "side"
    ] == "traitor"
    assert "Set Dave's side to Traitor" in interaction.messages[-1]


def test_set_side_reports_unsigned_member(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_set_side.callback(
            interaction, FakeMember(42, "Dave"), SimpleNamespace(value="traitor")
        )
    )

    assert "isn't signed up" in interaction.messages[-1]


# --- set-format -------------------------------------------------------------


def test_set_format_overrides(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _signup_store(paths).add_signup(
        str(GUILD_ID), EVENT_DATE, "42", "Dave", format_pref="solo"
    )
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_set_format.callback(
            interaction, FakeMember(42, "Dave"), SimpleNamespace(value="doubles")
        )
    )

    assert _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE)["42"][
        "format_pref"
    ] == "doubles"
    assert "Set Dave's format to Doubles (team up)" in interaction.messages[-1]


def test_set_format_preserves_signed_up_at(monkeypatch, tmp_path: Path) -> None:
    # The dedicated setter (unlike a re-signup) must not reorder the member by
    # bumping ``signed_up_at`` — that is why this exists alongside add-player.
    paths = _patch_environment(monkeypatch, tmp_path)
    _signup_store(paths).add_signup(
        str(GUILD_ID), EVENT_DATE, "42", "Dave", format_pref="solo"
    )
    before = _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE)["42"][
        "signed_up_at"
    ]
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_set_format.callback(
            interaction, FakeMember(42, "Dave"), SimpleNamespace(value="either")
        )
    )

    after = _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE)["42"]
    assert after["signed_up_at"] == before
    assert after["format_pref"] == "either"


def test_set_format_reports_unsigned_member(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_set_format.callback(
            interaction, FakeMember(42, "Dave"), SimpleNamespace(value="doubles")
        )
    )

    assert "isn't signed up" in interaction.messages[-1]


# --- lock-pair / avoid-pair -------------------------------------------------


def test_lock_pair_records_constraint(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_lock_pair.callback(
            interaction, FakeMember(42, "Dave"), FakeMember(43, "Cei"), False
        )
    )

    constraints = _constraints_store(paths).get_constraints(str(GUILD_ID), EVENT_DATE)
    assert constraints.locked_pairs == frozenset({frozenset({"42", "43"})})
    assert "Locked: Dave and Cei" in interaction.messages[-1]


def test_lock_pair_clear_removes_constraint(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _constraints_store(paths).add_lock(str(GUILD_ID), EVENT_DATE, "42", "43")
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_lock_pair.callback(
            interaction, FakeMember(42, "Dave"), FakeMember(43, "Cei"), True
        )
    )

    constraints = _constraints_store(paths).get_constraints(str(GUILD_ID), EVENT_DATE)
    assert constraints.locked_pairs == frozenset()
    assert "Lock removed" in interaction.messages[-1]


def test_lock_pair_rejects_self_pair(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_lock_pair.callback(
            interaction, FakeMember(42, "Dave"), FakeMember(42, "Dave"), False
        )
    )

    assert _constraints_store(paths).get_constraints(
        str(GUILD_ID), EVENT_DATE
    ).locked_pairs == frozenset()
    assert "two different players" in interaction.messages[-1]


def test_lock_pair_store_error_reports_constraint_message(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    paths.constraints.mkdir(parents=True)
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_lock_pair.callback(
            interaction, FakeMember(42, "Dave"), FakeMember(43, "Cei"), False
        )
    )

    assert interaction.messages[-1] == pairing_commands.CONSTRAINT_STORE_ERROR_MESSAGE


def test_avoid_pair_records_constraint(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_avoid_pair.callback(
            interaction, FakeMember(42, "Dave"), FakeMember(43, "Cei"), False
        )
    )

    constraints = _constraints_store(paths).get_constraints(str(GUILD_ID), EVENT_DATE)
    assert constraints.avoided_pairs == frozenset({frozenset({"42", "43"})})
    assert "Avoid set" in interaction.messages[-1]


def test_avoid_pair_store_error_reports_constraint_message(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    paths.constraints.mkdir(parents=True)
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_avoid_pair.callback(
            interaction, FakeMember(42, "Dave"), FakeMember(43, "Cei"), False
        )
    )

    assert interaction.messages[-1] == pairing_commands.CONSTRAINT_STORE_ERROR_MESSAGE


# --- set-bye ----------------------------------------------------------------


def test_set_bye_sets_forced_bye(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_set_bye.callback(interaction, FakeMember(42, "Dave"), False)
    )

    assert _constraints_store(paths).get_constraints(
        str(GUILD_ID), EVENT_DATE
    ).forced_bye == "42"
    assert "Dave will sit out" in interaction.messages[-1]


def test_set_bye_clear_removes_forced_bye(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _constraints_store(paths).set_bye(str(GUILD_ID), EVENT_DATE, "42")
    interaction = FakeInteraction()

    asyncio.run(admin.club_night_set_bye.callback(interaction, None, True))

    assert _constraints_store(paths).get_constraints(
        str(GUILD_ID), EVENT_DATE
    ).forced_bye is None
    assert "Forced bye cleared" in interaction.messages[-1]


def test_set_bye_without_member_prompts(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    asyncio.run(admin.club_night_set_bye.callback(interaction, None, False))

    assert "Choose a member" in interaction.messages[-1]


def test_set_bye_store_error_reports_constraint_message(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    paths.constraints.mkdir(parents=True)
    interaction = FakeInteraction()

    asyncio.run(
        admin.club_night_set_bye.callback(interaction, FakeMember(42, "Dave"), False)
    )

    assert interaction.messages[-1] == pairing_commands.CONSTRAINT_STORE_ERROR_MESSAGE


# --- clear-signups confirmation view ---------------------------------------


def test_clear_signups_posts_confirmation_view(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    asyncio.run(admin.club_night_clear_signups.callback(interaction))

    assert len(interaction.views) == 1
    assert isinstance(interaction.views[0], admin.ClearSignupsConfirmView)
    assert "cannot be undone" in interaction.messages[-1]


def test_clear_signups_confirm_wipes_both_stores(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _signup_store(paths).add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    _signup_store(paths).add_signup(str(GUILD_ID), EVENT_DATE, "43", "Cei")
    _constraints_store(paths).add_lock(str(GUILD_ID), EVENT_DATE, "42", "43")

    view = admin.ClearSignupsConfirmView(
        owner_user_id=11,
        guild_id=str(GUILD_ID),
        event_date=EVENT_DATE,
        title="Organised Play",
        club_night=CLUB_NIGHT,
    )
    component = FakeComponentInteraction(user_id=11)

    asyncio.run(view.confirm_button.callback(component))

    assert _signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE) == {}
    assert _constraints_store(paths).get_constraints(
        str(GUILD_ID), EVENT_DATE
    ).locked_pairs == frozenset()
    assert "Cleared 2 sign-ups" in component.edits[-1]["content"]


def test_clear_signups_constraints_failure_keeps_signups(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _signup_store(paths).add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    paths.constraints.mkdir(parents=True)

    view = admin.ClearSignupsConfirmView(
        owner_user_id=11,
        guild_id=str(GUILD_ID),
        event_date=EVENT_DATE,
        title="Organised Play",
        club_night=CLUB_NIGHT,
    )
    component = FakeComponentInteraction(user_id=11)

    asyncio.run(view.confirm_button.callback(component))

    assert set(_signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE)) == {"42"}
    assert (
        component.edits[-1]["content"]
        == pairing_commands.CONSTRAINT_STORE_ERROR_MESSAGE
    )


def test_clear_signups_cancel_keeps_data(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _signup_store(paths).add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")

    view = admin.ClearSignupsConfirmView(
        owner_user_id=11,
        guild_id=str(GUILD_ID),
        event_date=EVENT_DATE,
        title="Organised Play",
        club_night=CLUB_NIGHT,
    )
    component = FakeComponentInteraction(user_id=11)

    asyncio.run(view.cancel_button.callback(component))

    assert set(_signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE)) == {"42"}
    assert "Cancelled" in component.edits[-1]["content"]


def test_clear_signups_rejects_non_owner(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _signup_store(paths).add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")

    view = admin.ClearSignupsConfirmView(
        owner_user_id=11,
        guild_id=str(GUILD_ID),
        event_date=EVENT_DATE,
        title="Organised Play",
        club_night=CLUB_NIGHT,
    )
    component = FakeComponentInteraction(user_id=99)

    asyncio.run(view.confirm_button.callback(component))

    assert set(_signup_store(paths).get_signups(str(GUILD_ID), EVENT_DATE)) == {"42"}
    assert component.rejections
    assert component.edits == []


# --- preview-pairings view --------------------------------------------------


def test_preview_pairings_posts_view(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    store = _signup_store(paths)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "43", "Cei")
    interaction = FakeInteraction()

    asyncio.run(admin.club_night_preview_pairings.callback(interaction))

    assert len(interaction.views) == 1
    assert isinstance(interaction.views[0], pairing_commands.PairingsPreviewView)
    assert "Preview — not yet posted" in interaction.messages[-1]


def test_preview_pairings_too_few_players(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    _signup_store(paths).add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    interaction = FakeInteraction()

    asyncio.run(admin.club_night_preview_pairings.callback(interaction))

    assert interaction.views == []
    assert "nothing to pair" in interaction.messages[-1]


def test_preview_content_stays_within_discord_limit(
    monkeypatch, tmp_path: Path
) -> None:
    from bot.services.text_utils import DISCORD_MESSAGE_LIMIT

    paths = _patch_environment(monkeypatch, tmp_path)
    store = _signup_store(paths)
    for i in range(1, 41):
        store.add_signup(
            str(GUILD_ID),
            EVENT_DATE,
            str(i),
            f"A long club member display name number {i}",
        )
    interaction = FakeInteraction()
    asyncio.run(admin.club_night_preview_pairings.callback(interaction))
    view = interaction.views[0]

    assert len(view.render()) <= DISCORD_MESSAGE_LIMIT
    assert (
        len(view._compose_content(pairing_commands.PAIRINGS_HISTORY_WARNING))
        <= DISCORD_MESSAGE_LIMIT
    )


def test_preview_pairings_constraints_read_error_creates_no_view(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    store = _signup_store(paths)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "43", "Cei")
    # A directory at the constraints path makes the authoritative read raise.
    paths.constraints.mkdir(parents=True)
    interaction = FakeInteraction()

    asyncio.run(admin.club_night_preview_pairings.callback(interaction))

    assert interaction.views == []
    assert interaction.messages[-1] == pairing_commands.CONSTRAINTS_ERROR_MESSAGE


def test_preview_pairings_corrupt_constraints_create_no_view(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    store = _signup_store(paths)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "43", "Cei")
    paths.constraints.parent.mkdir(parents=True, exist_ok=True)
    paths.constraints.write_text("not json{", encoding="utf-8")
    interaction = FakeInteraction()

    asyncio.run(admin.club_night_preview_pairings.callback(interaction))

    assert interaction.views == []
    assert interaction.messages[-1] == pairing_commands.CONSTRAINTS_ERROR_MESSAGE


def test_preview_post_button_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    store = _signup_store(paths)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "43", "Cei")
    interaction = FakeInteraction()
    asyncio.run(admin.club_night_preview_pairings.callback(interaction))
    view = interaction.views[0]

    channel_sends: list[str] = []

    async def _channel_send(message: str, *, allowed_mentions: object = None) -> None:
        channel_sends.append(message)

    channel = SimpleNamespace(send=_channel_send)
    first = FakeComponentInteraction(user_id=11, channel=channel)
    second = FakeComponentInteraction(user_id=11, channel=channel)

    asyncio.run(view.post_button.callback(first))
    asyncio.run(view.post_button.callback(second))

    assert len(channel_sends) == 1
    assert second.rejections
    assert second.edits == []


def test_preview_reroll_while_posting_is_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    store = _signup_store(paths)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "43", "Cei")
    interaction = FakeInteraction()
    asyncio.run(admin.club_night_preview_pairings.callback(interaction))
    view = interaction.views[0]
    original_result = view._result
    view._posting = True

    component = FakeComponentInteraction(user_id=11)
    asyncio.run(view.reroll_button.callback(component))

    assert view._result == original_result
    assert component.rejections == ["These pairings are already being posted."]
    assert component.edits == []


def test_preview_post_failure_records_no_history(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    store = _signup_store(paths)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "43", "Cei")
    interaction = FakeInteraction()
    asyncio.run(admin.club_night_preview_pairings.callback(interaction))
    view = interaction.views[0]

    async def _failing_send(message: str, *, allowed_mentions: object = None) -> None:
        raise RuntimeError("channel send failed")

    component = FakeComponentInteraction(
        user_id=11, channel=SimpleNamespace(send=_failing_send)
    )

    with pytest.raises(RuntimeError, match="channel send failed"):
        asyncio.run(view.post_button.callback(component))

    assert not paths.history.exists()
    # The view is left re-postable after a transient failure.
    assert view._posting is False
    assert view._posted is False


def test_preview_post_button_publishes_and_records(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    store = _signup_store(paths)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "43", "Cei")
    interaction = FakeInteraction()
    asyncio.run(admin.club_night_preview_pairings.callback(interaction))
    view = interaction.views[0]

    channel_sends: list[str] = []

    async def _channel_send(message: str, *, allowed_mentions: object = None) -> None:
        channel_sends.append(message)

    component = FakeComponentInteraction(
        user_id=11, channel=SimpleNamespace(send=_channel_send)
    )

    asyncio.run(view.post_button.callback(component))

    assert any("Club night pairings" in msg for msg in channel_sends)
    history = pairing_commands._pairing_history_store()
    assert history.get_recent_opponents(
        str(GUILD_ID), date(2026, 7, 1), lookback=2
    ) == frozenset({frozenset({"42", "43"})})


def test_preview_reroll_after_post_is_rejected(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    store = _signup_store(paths)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "43", "Cei")
    interaction = FakeInteraction()
    asyncio.run(admin.club_night_preview_pairings.callback(interaction))
    view = interaction.views[0]

    async def _channel_send(message: str, *, allowed_mentions: object = None) -> None:
        pass

    post_component = FakeComponentInteraction(
        user_id=11, channel=SimpleNamespace(send=_channel_send)
    )
    asyncio.run(view.post_button.callback(post_component))
    original_result = view._result

    reroll_component = FakeComponentInteraction(user_id=11)
    asyncio.run(view.reroll_button.callback(reroll_component))

    assert view._result == original_result
    assert reroll_component.rejections == ["These pairings have already been posted."]
    assert reroll_component.edits == []


def test_preview_reroll_rejects_non_owner(monkeypatch, tmp_path: Path) -> None:
    paths = _patch_environment(monkeypatch, tmp_path)
    store = _signup_store(paths)
    store.add_signup(str(GUILD_ID), EVENT_DATE, "42", "Dave")
    store.add_signup(str(GUILD_ID), EVENT_DATE, "43", "Cei")
    interaction = FakeInteraction()
    asyncio.run(admin.club_night_preview_pairings.callback(interaction))
    view = interaction.views[0]

    component = FakeComponentInteraction(user_id=99)
    asyncio.run(view.reroll_button.callback(component))

    assert component.rejections
    assert component.edits == []
