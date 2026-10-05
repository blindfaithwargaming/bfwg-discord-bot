import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import discord

from bot.commands import club_night_games as game_commands
from bot.commands import interaction_helpers
from bot.services.club_night_games import GameStore
from bot.services.stadium_events import ClubNight


GUILD_ID = 123456789012345678
FORUM_CHANNEL_ID = 999
THREAD_ID = 555
EVENT_DATE = date(2026, 7, 8)
THREAD_TITLE = "Organised Play - 8th July"
CLUB_NIGHT = ClubNight(title=THREAD_TITLE, event_date=EVENT_DATE)


def setup_function() -> None:
    interaction_helpers._command_rate_limiter.reset()


def _member(user_id: int, name: str, *, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, display_name=name, bot=bot)


class FakeInteraction:
    def __init__(
        self,
        *,
        user_id: int = 11,
        display_name: str = "Aaron",
        guild_id: int | None = GUILD_ID,
        administrator: bool = False,
        thread_name: str = THREAD_TITLE,
        parent_id: int | None = FORUM_CHANNEL_ID,
        channel_send_error: Exception | None = None,
    ) -> None:
        self.user = SimpleNamespace(
            id=user_id,
            display_name=display_name,
            guild_permissions=SimpleNamespace(administrator=administrator),
        )
        self.guild_id = guild_id
        self.channel_id = THREAD_ID
        self.client = SimpleNamespace()
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
            if channel_send_error is not None:
                raise channel_send_error
            self.channel_sends.append((message, allowed_mentions))

        channel_attrs: dict[str, object] = {
            "send": _channel_send,
            "id": THREAD_ID,
            "name": thread_name,
            "created_at": datetime(2026, 6, 25, tzinfo=UTC),
        }
        if parent_id is not None:
            channel_attrs["parent_id"] = parent_id
        self.channel = SimpleNamespace(**channel_attrs)


def _patch_environment(monkeypatch, tmp_path: Path) -> Path:
    state_path = tmp_path / "state" / "club_night_games.json"
    monkeypatch.setattr(
        game_commands,
        "settings",
        SimpleNamespace(
            club_night_forum_channel_id=FORUM_CHANNEL_ID,
            club_night_games_state_path=str(state_path),
            summaries_dir=str(tmp_path / "summaries"),
            gcs_state_upload_enabled=False,
            gcs_state_bucket=None,
        ),
    )
    return state_path


def _run_report(interaction: FakeInteraction, **overrides) -> None:
    kwargs = {
        "result": SimpleNamespace(value="side1"),
        "side1_player1": _member(11, "Aaron"),
        "side1_faction1": "Sons of Horus",
        "side2_player1": _member(22, "Dave"),
        "side2_faction1": "Imperial Fists",
    }
    kwargs.update(overrides)
    asyncio.run(game_commands.club_night_report_game.callback(interaction, **kwargs))


def _run_remove(interaction: FakeInteraction, game_id: str) -> None:
    asyncio.run(game_commands.club_night_remove_game.callback(interaction, game_id))


def _run_games(interaction: FakeInteraction) -> None:
    asyncio.run(game_commands.club_night_games.callback(interaction))


def _stored_games(state_path: Path) -> list[dict]:
    return GameStore(state_path).get_games_for_thread(str(GUILD_ID), str(THREAD_ID))


def test_report_game_records_and_posts_publicly(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_report(
        interaction,
        points_size=3000,
        mission="Onslaught",
        notes="Close game",
        campaign_hex=12,
    )

    games = _stored_games(state_path)
    assert len(games) == 1
    game = games[0]
    assert game["club_night_date"] == "2026-07-08"
    assert game["thread_title"] == THREAD_TITLE
    assert game["reported_by_user_id"] == "11"
    assert game["result"] == "side1"
    assert game["points_size"] == 3000
    assert game["mission"] == "Onslaught"
    assert game["notes"] == "Close game"
    assert game["campaign_hex"] == 12
    assert [p["user_id"] for p in game["players"]] == ["11", "22"]

    assert len(interaction.channel_sends) == 1
    public_message, allowed = interaction.channel_sends[0]
    assert "Game reported — side 1 won" in public_message
    assert "Aaron (Sons of Horus)" in public_message
    assert "Dave (Imperial Fists)" in public_message
    assert "3000 points" in public_message
    assert "Mission: Onslaught" in public_message
    assert "Campaign hex: 12" in public_message
    assert "Notes: Close game" in public_message
    assert f"Game ref: `{game['id'][:8]}`" in public_message
    assert allowed is game_commands.NO_MENTIONS

    assert len(interaction.followups) == 1
    ephemeral_message, ephemeral, _ = interaction.followups[0]
    assert game["id"] in ephemeral_message
    assert ephemeral is True


def test_report_game_escapes_public_user_text(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_report(
        interaction,
        side1_player1=_member(11, "Aa_ron"),
        side1_faction1="Sons *of* Horus",
    )

    public_message, _ = interaction.channel_sends[0]
    assert "Aa\\_ron" in public_message
    assert "Sons \\*of\\* Horus" in public_message


def test_report_game_multiplayer_records_all_players(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_report(
        interaction,
        result=SimpleNamespace(value="draw"),
        side1_player2=_member(33, "Beth"),
        side1_faction2="World Eaters",
        side2_player2=_member(44, "Carl"),
        side2_faction2="Blood Angels",
    )

    game = _stored_games(state_path)[0]
    assert [p["user_id"] for p in game["players"]] == ["11", "33", "22", "44"]
    assert all(p["result"] == "draw" for p in game["players"])
    assert "Game reported — draw" in interaction.channel_sends[0][0]


def test_report_game_outside_thread_rejected(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(parent_id=None)

    _run_report(interaction)

    assert interaction.followups == [
        (
            game_commands.NOT_CLUB_NIGHT_THREAD_MESSAGE,
            True,
            game_commands.NO_MENTIONS,
        )
    ]
    assert interaction.channel_sends == []
    assert not state_path.exists()


def test_report_game_wrong_forum_rejected(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(parent_id=777)

    _run_report(interaction)

    assert interaction.followups[0][0] == game_commands.NOT_CLUB_NIGHT_THREAD_MESSAGE


def test_report_game_unparseable_thread_title_rejected(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(thread_name="General chat")

    _run_report(interaction)

    assert interaction.followups[0][0] == game_commands.NOT_CLUB_NIGHT_THREAD_MESSAGE


def test_report_game_outside_guild_rejected(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(guild_id=None)

    _run_report(interaction)

    assert interaction.followups == [
        (game_commands.GUILD_ONLY_MESSAGE, True, game_commands.NO_MENTIONS)
    ]


def test_report_game_heresy_player_without_faction_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_report(interaction, side1_player2=_member(33, "Sam"))

    assert (
        interaction.followups[0][0]
        == "Side 1 player 2 needs both a player and a faction."
    )
    assert _stored_games(state_path) == []
    assert interaction.channel_sends == []


def test_report_game_other_game_player_without_faction_is_optional(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    _run_report(
        interaction,
        game=SimpleNamespace(value="old-world"),
        side1_faction1=None,
        side2_faction1=None,
    )
    games = _stored_games(state_path)
    assert {player["faction"] for player in games[0]["players"]} == {"Not specified"}
    assert interaction.channel_sends


def test_report_game_faction_without_player_rejected(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_report(interaction, side2_faction2="World Eaters")

    assert (
        interaction.followups[0][0]
        == "Side 2 player 2 needs both a player and a faction."
    )


def test_report_game_duplicate_player_rejected(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_report(interaction, side2_player1=_member(11, "Aaron"))

    assert "only appear once" in interaction.followups[0][0]
    assert interaction.channel_sends == []


def test_report_game_bot_player_rejected(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_report(interaction, side2_player1=_member(22, "BFWG Bot", bot=True))

    assert interaction.followups[0][0] == "Bots cannot play games."


def test_report_game_store_oserror_reports_ephemerally(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    state_path.parent.parent.mkdir(parents=True, exist_ok=True)
    # A file where the state directory should be makes mkdir raise OSError.
    state_path.parent.write_text("blocker", encoding="utf-8")
    interaction = FakeInteraction()

    _run_report(interaction)

    message, ephemeral, _ = interaction.followups[0]
    assert message == game_commands.GAME_STORE_ERROR_MESSAGE
    assert ephemeral is True
    assert interaction.channel_sends == []


def test_report_game_public_post_failure_keeps_record(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    error = discord.HTTPException(
        SimpleNamespace(status=403, reason="Forbidden"), "boom"
    )
    interaction = FakeInteraction(channel_send_error=error)

    _run_report(interaction)

    games = _stored_games(state_path)
    assert len(games) == 1
    message, ephemeral, _ = interaction.followups[0]
    assert "could not be posted" in message
    assert games[0]["id"] in message
    assert ephemeral is True


def test_report_game_schedules_state_upload(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    scheduled: list[str] = []
    monkeypatch.setattr(
        game_commands.state_sync,
        "schedule_upload",
        lambda path, settings: scheduled.append(path),
    )
    interaction = FakeInteraction()

    _run_report(interaction)

    assert scheduled == [game_commands.settings.club_night_games_state_path]


def test_report_game_store_failure_does_not_schedule_upload(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    state_path.parent.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.write_text("blocker", encoding="utf-8")
    scheduled: list[str] = []
    monkeypatch.setattr(
        game_commands.state_sync,
        "schedule_upload",
        lambda path, settings: scheduled.append(path),
    )
    interaction = FakeInteraction()

    _run_report(interaction)

    assert scheduled == []


def test_remove_game_schedules_state_upload(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    _run_report(interaction)
    game_id = _stored_games(state_path)[0]["id"]
    scheduled: list[str] = []
    monkeypatch.setattr(
        game_commands.state_sync,
        "schedule_upload",
        lambda path, settings: scheduled.append(path),
    )

    interaction_helpers._command_rate_limiter.reset()
    remover = FakeInteraction()
    _run_remove(remover, game_id)

    assert scheduled == [game_commands.settings.club_night_games_state_path]


def test_remove_game_by_reporter(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    _run_report(interaction)
    game_id = _stored_games(state_path)[0]["id"]

    interaction_helpers._command_rate_limiter.reset()
    remover = FakeInteraction()
    _run_remove(remover, game_id[:8])

    assert _stored_games(state_path) == []
    message, ephemeral, _ = remover.followups[0]
    assert f"Removed game `{game_id[:8]}`" in message
    assert "Aaron (Sons of Horus)" in message
    assert ephemeral is True


def test_remove_game_denied_for_non_reporter(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()
    _run_report(interaction)
    game_id = _stored_games(state_path)[0]["id"]

    remover = FakeInteraction(user_id=99, display_name="Mallory")
    _run_remove(remover, game_id)

    assert remover.followups[0][0] == game_commands.REMOVAL_NOT_ALLOWED_MESSAGE
    assert len(_stored_games(state_path)) == 1


def test_remove_game_allowed_for_admin(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(
        interaction_helpers, "settings", SimpleNamespace(bot_owner_user_ids=frozenset())
    )
    interaction = FakeInteraction()
    _run_report(interaction)
    game_id = _stored_games(state_path)[0]["id"]

    remover = FakeInteraction(user_id=99, display_name="Admin", administrator=True)
    _run_remove(remover, game_id)

    assert _stored_games(state_path) == []
    assert "Removed game" in remover.followups[0][0]


def test_remove_game_unknown_ref(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_remove(interaction, "f" * 32)

    assert interaction.followups[0][0] == game_commands.GAME_NOT_FOUND_MESSAGE


def test_remove_game_short_ref_rejected(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_remove(interaction, "abcd")

    assert "at least 8" in interaction.followups[0][0]


def test_games_list_empty(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction()

    _run_games(interaction)

    message, ephemeral, _ = interaction.followups[0]
    assert message == game_commands.NO_GAMES_MESSAGE
    assert ephemeral is True


def test_games_list_shows_recorded_games(monkeypatch, tmp_path: Path) -> None:
    state_path = _patch_environment(monkeypatch, tmp_path)
    reporter = FakeInteraction()
    _run_report(reporter)
    game_id = _stored_games(state_path)[0]["id"]

    interaction_helpers._command_rate_limiter.reset()
    interaction = FakeInteraction()
    _run_games(interaction)

    message, ephemeral, _ = interaction.followups[0]
    assert THREAD_TITLE in message
    assert "1 game recorded" in message
    assert f"`{game_id[:8]}`" in message
    assert "side 1 won" in message
    assert "Aaron (Sons of Horus)" in message
    assert ephemeral is True


def test_games_list_outside_thread_rejected(monkeypatch, tmp_path: Path) -> None:
    _patch_environment(monkeypatch, tmp_path)
    interaction = FakeInteraction(parent_id=None)

    _run_games(interaction)

    assert interaction.followups[0][0] == game_commands.NOT_CLUB_NIGHT_THREAD_MESSAGE


def test_build_game_confirmation_text_draw_has_no_winner_marker() -> None:
    entry = {
        "id": "a" * 32,
        "result": "draw",
        "players": [
            {"user_id": "11", "display_name": "Aaron", "faction": "Sons of Horus", "side": "side1", "result": "draw"},
            {"user_id": "22", "display_name": "Dave", "faction": "Imperial Fists", "side": "side2", "result": "draw"},
        ],
        "points_size": None,
        "mission": None,
        "notes": None,
    }

    text = game_commands._build_game_confirmation_text(entry=entry)

    assert "Game reported — draw" in text
    assert "(winner)" not in text
    assert "points" not in text
    assert "Mission:" not in text
    assert "Campaign hex:" not in text
    assert "Notes:" not in text


def test_build_game_confirmation_text_marks_winning_side() -> None:
    entry = {
        "id": "b" * 32,
        "result": "side2",
        "players": [
            {"user_id": "11", "display_name": "Aaron", "faction": "Sons of Horus", "side": "side1", "result": "loss"},
            {"user_id": "22", "display_name": "Dave", "faction": "Imperial Fists", "side": "side2", "result": "win"},
        ],
    }

    text = game_commands._build_game_confirmation_text(entry=entry)

    assert "Side 2 (winner): Dave (Imperial Fists)" in text
    assert "Side 1: Aaron (Sons of Horus)" in text


def test_build_games_list_text_lists_each_game() -> None:
    games = [
        {
            "id": "c" * 32,
            "result": "side1",
            "players": [
                {"display_name": "Aaron", "faction": "Sons of Horus", "side": "side1"},
                {"display_name": "Dave", "faction": "Imperial Fists", "side": "side2"},
            ],
        },
        {
            "id": "d" * 32,
            "result": "draw",
            "players": [
                {"display_name": "Beth", "faction": "World Eaters", "side": "side1"},
                {"display_name": "Carl", "faction": "Blood Angels", "side": "side2"},
            ],
        },
    ]

    text = game_commands._build_games_list_text(club_night=CLUB_NIGHT, games=games)

    assert "2 games recorded" in text
    assert (
        f"`{'c' * 8}` Horus Heresy — side 1 won: Aaron (Sons of Horus) vs Dave (Imperial Fists)"
        in text
    )
    assert (
        f"`{'d' * 8}` Horus Heresy — draw: Beth (World Eaters) vs Carl (Blood Angels)"
        in text
    )
