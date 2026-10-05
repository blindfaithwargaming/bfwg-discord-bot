import asyncio
import inspect
import json
import random
from datetime import UTC, date, datetime
from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

from bot.commands import club_night_admin as admin
from bot.commands import club_night_announcement as announcement
from bot.commands import club_night_games as games
from bot.commands import club_night_pairings as commands
from bot.commands import club_night_reminder as reminder
from bot.commands import interaction_helpers
from bot.commands.club_night_group import _club_night_group
from bot.services.club_night_announcement import is_duplicate_announcement_title
from bot.services.club_night_forum import (
    find_next_announcement_club_night,
    find_latest_announcement_club_night,
    parse_thread_club_night,
)
from bot.services.club_night_games import GameStore
from bot.services.club_night_pairing_constraints import PairingConstraintsStore
from bot.services.club_night_pairing_history import PairingHistoryStore
from bot.services.club_night_pairings import generate_pairings, format_pairings
from bot.services.club_night_signups import SignupStore
from bot.services.organised_play import (
    BUSHIDO_FACTIONS,
    build_signup_instructions,
    signup_options,
)
from bot.services.stadium_events import ClubNight

pytestmark = pytest.mark.usefixtures("club_night_clock")
GUILD = "123456789012345678"
DAY = date(2026, 6, 25)
NOW = datetime(2026, 6, 12, 12, tzinfo=UTC)


def event(game="bushido", thread="555", day=DAY):
    return ClubNight(
        f"{game.title()} Organised Play - {day.day}th June", day, game, thread
    )


def thread(game="bushido", thread_id=555):
    return SimpleNamespace(
        id=thread_id, parent_id=999, name=event(game).title, created_at=NOW
    )


@pytest.mark.parametrize(
    "name,game",
    [
        ("Bushido", "bushido"),
        ("Heresy", "heresy"),
        ("Horus Heresy", "heresy"),
        ("", "heresy"),
        ("Warhammer 40,000", "warhammer 40,000"),
    ],
)
def test_thread_game_recognition(name, game):
    channel = thread()
    channel.name = f"{name} Organised Play - 25th June"
    found = parse_thread_club_night(channel, 999, today=DAY)
    assert found.game == game
    assert found.thread_id == "555"
    assert found.event_date == DAY
    assert parse_thread_club_night(channel, 123, today=DAY) is None


def test_schedulers_only_consider_heresy():
    bushido, heresy = thread(), thread("heresy", 556)
    bushido.name = "Bushido Organised Play - 20th June"
    assert (
        find_next_announcement_club_night([bushido, heresy], today=NOW.date()).title
        == heresy.name
    )
    bushido.name = "Bushido Organised Play - 30th June"
    assert (
        find_latest_announcement_club_night([bushido, heresy], today=NOW.date()).title
        == heresy.name
    )
    assert find_next_announcement_club_night([bushido], today=NOW.date()) is None
    assert find_latest_announcement_club_night([bushido], today=NOW.date()) is None


def test_legacy_title_dedup_does_not_confuse_games():
    assert is_duplicate_announcement_title(
        "Organised Play - 25th June", event("heresy").title
    )
    assert not is_duplicate_announcement_title(event().title, event("heresy").title)


def test_signups_and_constraints_isolate_threads_and_games(tmp_path):
    events = [event(), event("heresy", "556"), event("bushido", "557")]
    for i, night in enumerate(events):
        SignupStore(tmp_path / "s.json", event=night).add_signup(
            GUILD, DAY, str(i), str(i), now=NOW
        )
        PairingConstraintsStore(tmp_path / "c.json", event=night).set_bye(
            GUILD, DAY, str(i), now=NOW
        )
    for i, night in enumerate(events):
        assert set(
            SignupStore(tmp_path / "s.json", event=night).get_signups(GUILD, DAY)
        ) == {str(i)}
        assert PairingConstraintsStore(
            tmp_path / "c.json", event=night
        ).get_constraints(GUILD, DAY).forced_bye == str(i)
    SignupStore(tmp_path / "s.json", event=events[0]).clear_event(GUILD, DAY, now=NOW)
    assert SignupStore(tmp_path / "s.json", event=events[1]).get_signups(GUILD, DAY)


def test_legacy_state_is_claimed_once_by_heresy(tmp_path):
    path = tmp_path / "signups.json"
    SignupStore(path).add_signup(GUILD, DAY, "1", "One", now=NOW)
    assert not SignupStore(path, event=event()).get_signups(GUILD, DAY)
    first = SignupStore(path, event=event("heresy", "556"))
    assert "1" in first.get_signups(GUILD, DAY)
    first.add_signup(GUILD, DAY, "2", "Two", now=NOW)
    assert not SignupStore(path, event=event("heresy", "557")).get_signups(GUILD, DAY)
    assert DAY.isoformat() not in json.loads(path.read_text())[GUILD]


def test_history_is_per_game_and_keeps_same_day_threads(tmp_path):
    path = tmp_path / "history.json"
    earlier = date(2026, 6, 11)
    PairingHistoryStore(path).record_pairings(GUILD, earlier, [("h1", "h2")], now=NOW)
    for tid, pair in [("1", ("a", "b")), ("2", ("c", "d"))]:
        PairingHistoryStore(path, event=event(thread=tid, day=earlier)).record_pairings(
            GUILD, earlier, [pair], now=NOW
        )
    bushido = PairingHistoryStore(path, event=event())
    assert bushido.get_recent_opponents(GUILD, DAY, 2) == frozenset(
        [frozenset(["a", "b"]), frozenset(["c", "d"])]
    )
    assert PairingHistoryStore(path, event=event("heresy")).get_recent_opponents(
        GUILD, DAY, 2
    ) == frozenset([frozenset(["h1", "h2"])])
    assert not bushido.get_recent_opponents(GUILD, DAY, 0)


def test_history_retention_does_not_evict_another_game(tmp_path):
    path = tmp_path / "history.json"
    PairingHistoryStore(path).record_pairings(
        GUILD, date(2026, 1, 1), [("h1", "h2")], now=NOW
    )
    for i in range(1, 16):
        day = date(2026, 5, i)
        PairingHistoryStore(path, event=event(day=day)).record_pairings(
            GUILD, day, [("a", "b")], now=NOW
        )
    records = json.loads(path.read_text())[GUILD]
    assert len(records) == 13
    assert "2026-01-01" in records


def test_bushido_optional_details_survive_signup_updates_and_render(tmp_path):
    store = SignupStore(tmp_path / "s.json", event=event())
    details = signup_options(
        game="bushido",
        faction="ito clan",
        play_style="learning",
        rice=50,
        side=None,
        format_pref=None,
    )
    store.add_signup(GUILD, DAY, "1", "@everyone", details=details, now=NOW)
    store.add_signup(GUILD, DAY, "1", "@everyone", details={"game": "bushido"}, now=NOW)
    store.add_signup(GUILD, DAY, "2", "Two", details={"game": "bushido"}, now=NOW)
    signups = store.get_signups(GUILD, DAY)
    assert signups["1"]["faction"] == "Ito Clan"
    assert signups["1"]["rice"] == 50
    text = format_pairings(
        DAY, generate_pairings(signups, rng=random.Random(1)), signups
    )
    assert "Ito Clan" in text and "Learning" in text and "50 rice" in text
    assert "Loyalist" not in text and "Traitor" not in text
    assert "@everyone" not in text


@pytest.mark.parametrize(
    "override",
    [
        {"rice": 0},
        {"rice": 1001},
        {"faction": "Space Marines"},
        {"side": "loyalist"},
        {"format_pref": "doubles"},
        {"play_style": "bad"},
    ],
)
def test_invalid_bushido_options(override):
    kwargs = dict(
        game="bushido",
        faction=None,
        play_style=None,
        rice=None,
        side=None,
        format_pref=None,
    )
    with pytest.raises(ValueError):
        signup_options(**(kwargs | override))


def test_optional_details_do_not_block_pairing_and_rematches_are_avoided():
    signups = {
        str(i): {
            "game": "bushido",
            "side": "either",
            "format_pref": "solo",
            "rice": 25 * i,
            "play_style": "learning" if i % 2 else "competitive",
        }
        for i in range(1, 5)
    }
    recent = frozenset([frozenset(["1", "2"]), frozenset(["3", "4"])])
    for seed in range(10):
        result = generate_pairings(
            signups, rng=random.Random(seed), recent_opponents=recent
        )
        assert len(result.matches) == 2
        assert all(match.is_singles for match in result.matches)
        assert not set(result.opponent_pairs()) & recent


class Interaction:
    def __init__(self, *, game="bushido", user_id=11, administrator=True, channel=None):
        self.guild_id = int(GUILD)
        self.user = SimpleNamespace(
            id=user_id,
            display_name=f"Player {user_id}",
            bot=False,
            guild_permissions=SimpleNamespace(administrator=administrator),
        )
        self.channel = channel or thread(game)
        self.channel_id = self.channel.id
        self.messages, self.public, self.views = [], [], []
        self.permissions = SimpleNamespace(manage_threads=administrator)
        self.client = SimpleNamespace()
        self.deferred = False

        async def defer(**kwargs):
            self.deferred = True

        async def send(content, **kwargs):
            self.messages.append(content)
            if kwargs.get("view"):
                self.views.append(kwargs["view"])
            return SimpleNamespace(edit=send)

        async def publish(content, **kwargs):
            assert kwargs.get("allowed_mentions") is commands.NO_MENTIONS
            self.public.append(content)

        self.response = SimpleNamespace(
            is_done=lambda: self.deferred,
            defer=defer,
            send_message=send,
            edit_message=send,
        )
        self.followup = SimpleNamespace(send=send)
        self.channel.send = publish


@pytest.fixture
def environment(monkeypatch, tmp_path):
    config = SimpleNamespace(
        club_night_forum_channel_id=999,
        club_night_signup_state_path=tmp_path / "s.json",
        club_night_pairing_constraints_state_path=tmp_path / "c.json",
        club_night_pairing_history_state_path=tmp_path / "h.json",
        club_night_games_state_path=tmp_path / "g.json",
        club_night_pairing_lookback=2,
        summaries_dir=tmp_path / "summaries",
        bot_owner_user_ids=frozenset(),
        gcs_state_upload_enabled=False,
        gcs_state_bucket=None,
    )
    for module in (commands, admin, games, announcement, reminder, interaction_helpers):
        monkeypatch.setattr(module, "settings", config)
    return config


def run(command, interaction, **kwargs):
    interaction_helpers._command_rate_limiter.reset()
    asyncio.run(command.callback(interaction, **kwargs))


def test_bushido_workflow_from_instructions_to_results(environment):
    owner = Interaction()
    run(admin.club_night_signup_info, owner)
    assert "No options are required" in owner.public[0]
    assert "100 rice" in owner.public[0]
    for uid in range(11, 15):
        run(commands.club_night_signup, Interaction(user_id=uid, administrator=False))
    extra = Interaction(user_id=11, administrator=False)
    run(
        commands.club_night_signup,
        extra,
        faction="Ito Clan",
        rice=50,
        play_style=app_commands.Choice(name="Learning", value="learning"),
    )
    assert "50 rice" in extra.messages[0]
    run(admin.club_night_preview_pairings, owner)
    assert "Learning" in owner.views[0].render()
    run(commands.club_night_pairings, owner)
    assert "50 rice" in owner.public[-1]
    history = PairingHistoryStore(
        environment.club_night_pairing_history_state_path, event=event()
    )
    assert history.get_recent_opponents(GUILD, date(2026, 6, 26), 2)
    run(
        games.club_night_report_game,
        owner,
        result=app_commands.Choice(name="Draw", value="draw"),
        side1_player1=Interaction(user_id=11).user,
        side2_player1=Interaction(user_id=12).user,
        rice=50,
    )
    records = GameStore(environment.club_night_games_state_path).get_games_for_thread(
        GUILD, "555"
    )
    assert records[0]["game"] == "bushido" and records[0]["rice"] == 50
    assert "50 rice" in owner.public[-1]
    run(games.club_night_games, owner)
    assert "1 game recorded" in owner.messages[-1]
    run(games.club_night_remove_game, owner, game_id=records[0]["id"])
    assert not GameStore(environment.club_night_games_state_path).get_games_for_thread(
        GUILD, "555"
    )
    run(commands.club_night_withdraw, extra)
    assert "11" not in SignupStore(
        environment.club_night_signup_state_path, event=event()
    ).get_signups(GUILD, DAY)


@pytest.mark.parametrize("command", _club_night_group.commands, ids=lambda c: c.name)
def test_every_club_night_command_rejects_non_event_thread(environment, command):
    interaction = Interaction(
        channel=SimpleNamespace(id=555, parent_id=999, name="General chat")
    )
    kwargs = {}
    for name, param in inspect.signature(command.callback).parameters.items():
        if name == "interaction" or param.default is not inspect.Parameter.empty:
            continue
        if name in ("side", "format", "result"):
            kwargs[name] = app_commands.Choice(name="Example", value="either")
        elif name == "game_id":
            kwargs[name] = "12345678"
        else:
            kwargs[name] = interaction.user
    run(command, interaction, **kwargs)
    assert any(
        "inside a club-night thread" in message for message in interaction.messages
    )
    assert not interaction.public
    assert not list(environment.club_night_signup_state_path.parent.glob("*.json"))


def test_signup_info_requires_admin(environment):
    interaction = Interaction(administrator=False)
    run(admin.club_night_signup_info, interaction)
    assert not interaction.public
    assert any("admin" in message.lower() for message in interaction.messages)
    assert not interaction.deferred
    assert interaction_helpers._command_rate_limiter.acquire(interaction.user.id) == 0.0


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "no"),
            admin.SIGNUP_INFO_FORBIDDEN_MESSAGE,
        ),
        (
            discord.HTTPException(SimpleNamespace(status=500, reason="Err"), "x"),
            admin.SIGNUP_INFO_FAILED_MESSAGE,
        ),
    ],
)
def test_signup_info_reports_post_failure(environment, error, expected):
    interaction = Interaction()

    async def fail(content, **kwargs):
        raise error

    interaction.channel.send = fail
    run(admin.club_night_signup_info, interaction)
    assert interaction.messages == [expected]


def test_faction_autocomplete_is_event_specific(environment):
    interaction = Interaction()
    for autocomplete in (
        commands._signup_faction_autocomplete,
        games._faction_autocomplete,
    ):
        matches = asyncio.run(autocomplete(interaction, ""))
        assert {match.value for match in matches} == set(BUSHIDO_FACTIONS)
    assert not asyncio.run(games._mission_autocomplete(interaction, ""))


def test_remove_game_cannot_target_another_thread(environment):
    store = GameStore(environment.club_night_games_state_path)
    record = store.add_game(
        guild_id=GUILD,
        club_night_date=DAY,
        thread_id="777",
        thread_title=event().title,
        reported_by_user_id="11",
        result="draw",
        side1=[("11", "One", "Ito Clan")],
        side2=[("12", "Two", "Jung Pirates")],
        game="bushido",
    )
    interaction = Interaction()
    run(games.club_night_remove_game, interaction, game_id=record["id"])
    assert "No game found" in interaction.messages[-1]
    assert store.get_games_for_thread(GUILD, "777")


def test_signup_text_escapes_thread_title():
    text = build_signup_instructions(
        event=ClubNight("@everyone **Bushido**", DAY, "bushido")
    )
    assert "@everyone" not in text
    assert len(text) <= 2000


def test_expired_signup_rejected_but_results_remain_available(environment):
    channel = thread()
    channel.name = "Bushido Organised Play - 1st January"
    channel.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    interaction = Interaction(channel=channel)
    run(commands.club_night_signup, interaction)
    assert "60-day signup window" in interaction.messages[-1]
    assert not environment.club_night_signup_state_path.exists()
    run(
        games.club_night_report_game,
        interaction,
        result=app_commands.Choice(name="Draw", value="draw"),
        side1_player1=interaction.user,
        side2_player1=Interaction(user_id=12).user,
    )
    assert "Game recorded" in interaction.messages[-1]


def test_legacy_constraints_claimed_by_heresy_not_bushido(tmp_path):
    path = tmp_path / "constraints.json"
    PairingConstraintsStore(path).add_lock(GUILD, DAY, "1", "2", now=NOW)
    assert (
        not PairingConstraintsStore(path, event=event())
        .get_constraints(GUILD, DAY)
        .locked_pairs
    )
    heresy = PairingConstraintsStore(path, event=event("heresy", "556"))
    assert heresy.get_constraints(GUILD, DAY).locked_pairs == frozenset(
        [frozenset(["1", "2"])]
    )
    heresy.set_bye(GUILD, DAY, "3", now=NOW)
    assert (
        not PairingConstraintsStore(path, event=event("heresy", "557"))
        .get_constraints(GUILD, DAY)
        .locked_pairs
    )


def test_admin_clear_only_wipes_current_bushido_event(environment):
    first, second = event(), event("bushido", "556")
    for night in (first, second):
        SignupStore(environment.club_night_signup_state_path, event=night).add_signup(
            GUILD, DAY, "1", "One", now=NOW
        )
        PairingConstraintsStore(
            environment.club_night_pairing_constraints_state_path, event=night
        ).set_bye(GUILD, DAY, "1", now=NOW)
    interaction = Interaction()
    run(admin.club_night_clear_signups, interaction)
    view = interaction.views[0]
    asyncio.run(view.confirm_button.callback(interaction))
    assert not SignupStore(
        environment.club_night_signup_state_path, event=first
    ).get_signups(GUILD, DAY)
    assert SignupStore(
        environment.club_night_signup_state_path, event=second
    ).get_signups(GUILD, DAY)
    assert (
        PairingConstraintsStore(
            environment.club_night_pairing_constraints_state_path, event=second
        )
        .get_constraints(GUILD, DAY)
        .forced_bye
        == "1"
    )


def choice(value):
    return app_commands.Choice(name=value, value=value)


def test_game_choices_match_represented_games():
    from bot.services.organised_play import REPRESENTED_GAMES

    assert REPRESENTED_GAMES == {
        "heresy": "Horus Heresy",
        "40k-3rd": "Warhammer 40,000 3rd Edition",
        "bushido": "Bushido",
        "old-world": "Warhammer: The Old World",
    }
    for command in (
        commands.club_night_signup,
        admin.club_night_add_player,
        games.club_night_report_game,
    ):
        parameter = next(p for p in command.parameters if p.name == "game")
        assert not parameter.required
        assert {c.value: c.name for c in parameter.choices} == REPRESENTED_GAMES


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Warhammer 40,000 3rd Edition", "40k-3rd"),
        ("3rd Ed", "40k-3rd"),
        ("Warhammer: The Old World", "old-world"),
        ("Old World", "old-world"),
        ("Horus Heresy", "heresy"),
        ("Bushido", "bushido"),
    ],
)
def test_omitted_game_uses_represented_thread_title(environment, title, expected):
    channel = thread()
    channel.name = f"{title} Organised Play - 25th June"
    interaction = Interaction(channel=channel)
    run(commands.club_night_signup, interaction)
    night = parse_thread_club_night(channel, 999, today=NOW.date())
    entry = SignupStore(
        environment.club_night_signup_state_path, event=night
    ).get_signups(GUILD, DAY)["11"]
    assert entry["game"] == expected


def test_omitted_game_resets_to_title_and_clears_other_game_details(environment):
    interaction = Interaction()
    run(
        commands.club_night_signup,
        interaction,
        game=choice("heresy"),
        faction="Imperial Fists",
        side=choice("loyalist"),
        format=choice("doubles"),
    )
    store = SignupStore(environment.club_night_signup_state_path, event=event())
    assert store.get_signups(GUILD, DAY)["11"]["game"] == "heresy"
    run(commands.club_night_signup, interaction)
    entry = store.get_signups(GUILD, DAY)["11"]
    assert entry["game"] == "bushido"
    assert entry["side"] == "either" and entry["format_pref"] == "solo"
    assert "faction" not in entry and "rice" not in entry
    assert len(store.get_signups(GUILD, DAY)) == 1
    assert "Playing Bushido" in interaction.messages[-1]
    run(
        commands.club_night_signup,
        interaction,
        faction="Ito Clan",
        rice=50,
        play_style=choice("learning"),
    )
    run(commands.club_night_signup, interaction, game=choice("old-world"))
    entry = store.get_signups(GUILD, DAY)["11"]
    assert entry["game"] == "old-world"
    assert not {"faction", "rice", "play_style"} & entry.keys()


def test_same_game_update_preserves_optional_details(environment):
    interaction = Interaction(game="heresy")
    run(
        commands.club_night_signup,
        interaction,
        game=choice("bushido"),
        faction="Ito Clan",
        rice=75,
    )
    run(
        commands.club_night_signup,
        interaction,
        game=choice("bushido"),
        play_style=choice("competitive"),
    )
    entry = SignupStore(
        environment.club_night_signup_state_path, event=event("heresy")
    ).get_signups(GUILD, DAY)["11"]
    assert entry["rice"] == 75 and entry["faction"] == "Ito Clan"
    assert entry["play_style"] == "competitive"


def test_one_signup_per_date_moves_between_threads_but_preserves_other_dates_and_guilds(
    tmp_path,
):
    path = tmp_path / "s.json"
    first = SignupStore(path, event=event("heresy", "111"))
    second = SignupStore(path, event=event("bushido", "222"))
    first.add_signup(GUILD, DAY, "1", "One", now=NOW)
    first.add_signup(GUILD, date(2026, 6, 26), "1", "One", now=NOW)
    other_guild = "223456789012345678"
    first.add_signup(other_guild, DAY, "1", "One", now=NOW)
    second.add_signup(GUILD, DAY, "1", "One", details={"game": "old-world"}, now=NOW)
    assert not first.get_signups(GUILD, DAY)
    assert first.get_signups(GUILD, date(2026, 6, 26))
    assert first.get_signups(other_guild, DAY)
    assert second.get_signups(GUILD, DAY)["1"]["game"] == "old-world"
    first.add_signup(GUILD, DAY, "1", "One", now=NOW)
    assert not second.get_signups(GUILD, DAY)
    assert first.get_signups(GUILD, DAY)["1"]["game"] == "heresy"


def test_mixed_game_pairings_ignore_cross_game_locks_and_preferences():
    signups = {
        str(i): {"game": game, "format_pref": "doubles", "side": "loyalist"}
        for i, game in enumerate(
            [
                "heresy",
                "heresy",
                "heresy",
                "bushido",
                "bushido",
                "bushido",
                "old-world",
                "old-world",
            ]
        )
    }
    signups["0"]["preferred_user_id"] = "3"
    signups["3"]["preferred_user_id"] = "0"
    locked = frozenset([frozenset(["1", "4"])])
    for seed in range(10):
        result = generate_pairings(
            signups, rng=random.Random(seed), locked_pairs=locked
        )
        assert set(result.participants) == set(signups)
        for match in result.matches:
            played = {signups[p]["game"] for p in match.players}
            assert len(played) == 1
            if "heresy" not in played:
                assert match.is_singles
        assert any(signups[p]["game"] == "bushido" for p in result.byes)
    assert "Locks between different games" in format_pairings(
        DAY, result, signups, locked_pairs=locked
    )


def test_mixed_history_uses_selected_game_and_replaces_all_games_for_thread(tmp_path):
    path = tmp_path / "h.json"
    earlier = date(2026, 6, 11)
    hosted_by_heresy = PairingHistoryStore(path, event=event("heresy", "111", earlier))
    signups = {
        "1": {"game": "bushido"},
        "2": {"game": "bushido"},
        "3": {"game": "heresy"},
        "4": {"game": "heresy"},
    }
    hosted_by_heresy.record_pairings(
        GUILD, earlier, [("1", "2"), ("3", "4")], signups=signups, now=NOW
    )
    current = PairingHistoryStore(path, event=event())
    assert current.get_recent_for_signups(GUILD, DAY, 2, signups) == frozenset(
        [frozenset(["1", "2"]), frozenset(["3", "4"])]
    )
    switched = {player: {"game": "old-world"} for player in signups}
    assert not current.get_recent_for_signups(GUILD, DAY, 2, switched)
    other_thread = PairingHistoryStore(path, event=event("bushido", "222", earlier))
    other_thread.record_pairings(GUILD, earlier, [("5", "6")], now=NOW)
    hosted_by_heresy.record_pairings(
        GUILD, earlier, [("1", "3"), ("2", "4")], signups=switched, now=NOW
    )
    data = json.loads(path.read_text())[GUILD]
    assert set(data) == {"2026-06-11|old-world|111", "2026-06-11|bushido|222"}
    assert current.get_recent_opponents(GUILD, DAY, 2) == frozenset(
        [frozenset(["5", "6"])]
    )


def test_game_selection_drives_autocomplete_in_other_game_thread(environment):
    interaction = Interaction(game="heresy")
    interaction.namespace = SimpleNamespace(game="bushido")
    for autocomplete in (
        commands._signup_faction_autocomplete,
        games._faction_autocomplete,
    ):
        matches = asyncio.run(autocomplete(interaction, "ito"))
        assert [match.value for match in matches] == ["Ito Clan"]
    assert not asyncio.run(games._mission_autocomplete(interaction, ""))
    interaction.namespace.game = "old-world"
    assert not asyncio.run(commands._signup_faction_autocomplete(interaction, ""))


def test_results_record_selected_game_not_host_thread(environment):
    interaction = Interaction(game="heresy")
    run(
        games.club_night_report_game,
        interaction,
        result=choice("draw"),
        side1_player1=interaction.user,
        side2_player1=Interaction(user_id=12).user,
        game=choice("bushido"),
        rice=75,
        side1_faction1="Ito Clan",
        side2_faction1="Jung Pirates",
    )
    entry = GameStore(environment.club_night_games_state_path).get_games_for_thread(
        GUILD, "555"
    )[0]
    assert entry["game"] == "bushido" and entry["rice"] == 75
    assert "Game: Bushido" in interaction.public[-1]
    run(games.club_night_games, interaction)
    assert "Bushido" in interaction.messages[-1]


def test_admin_side_and_format_follow_players_selected_game(environment):
    interaction = Interaction()
    run(
        admin.club_night_add_player,
        interaction,
        member=interaction.user,
        game=choice("heresy"),
    )
    run(
        admin.club_night_set_side,
        interaction,
        member=interaction.user,
        side=choice("loyalist"),
    )
    run(
        admin.club_night_set_format,
        interaction,
        member=interaction.user,
        format=choice("doubles"),
    )
    store = SignupStore(environment.club_night_signup_state_path, event=event())
    assert store.get_signups(GUILD, DAY)["11"]["format_pref"] == "doubles"
    run(admin.club_night_add_player, interaction, member=interaction.user)
    run(
        admin.club_night_set_side,
        interaction,
        member=interaction.user,
        side=choice("loyalist"),
    )
    assert "only used for Heresy signups" in interaction.messages[-1]
    assert store.get_signups(GUILD, DAY)["11"]["side"] == "either"


def test_preview_cannot_post_after_player_changes_games(environment):
    owner = Interaction()
    for uid in (11, 12):
        run(commands.club_night_signup, Interaction(user_id=uid))
    run(admin.club_night_preview_pairings, owner)
    view = owner.views[-1]
    run(commands.club_night_signup, Interaction(user_id=12), game=choice("old-world"))
    asyncio.run(view.post_button.callback(owner))
    assert not owner.public
    assert "Signups or constraints changed" in owner.messages[-1]
    run(admin.club_night_preview_pairings, owner)
    fresh = owner.views[-1]
    assert not fresh._result.matches and len(fresh._result.byes) == 2
    asyncio.run(fresh.post_button.callback(owner))
    assert "Bushido" in owner.public[-1] and "The Old World" in owner.public[-1]


def test_game_option_validates_before_replacing_existing_signup(environment):
    interaction = Interaction()
    run(commands.club_night_signup, interaction, rice=50)
    run(commands.club_night_signup, interaction, game=choice("unrepresented"))
    entry = SignupStore(
        environment.club_night_signup_state_path, event=event()
    ).get_signups(GUILD, DAY)["11"]
    assert entry["game"] == "bushido" and entry["rice"] == 50
    assert "represented games" in interaction.messages[-1]


def test_mixed_event_posts_history_by_selected_game_and_avoids_rematches(environment):
    prior = PairingHistoryStore(
        environment.club_night_pairing_history_state_path,
        event=event("bushido", "444", date(2026, 6, 11)),
    )
    prior.record_pairings(
        GUILD, date(2026, 6, 11), [("11", "12"), ("13", "14")], now=NOW
    )
    owner = Interaction(game="heresy")
    for uid in range(11, 17):
        interaction = Interaction(game="heresy", user_id=uid)
        run(
            commands.club_night_signup,
            interaction,
            game=choice("bushido") if uid < 15 else None,
        )
    run(commands.club_night_pairings, owner)
    data = json.loads(environment.club_night_pairing_history_state_path.read_text())[
        GUILD
    ]
    pairs = {frozenset(pair) for pair in data["2026-06-25|bushido|555"]["pairs"]}
    assert len(pairs) == 2
    assert not pairs & {frozenset(["11", "12"]), frozenset(["13", "14"])}
    assert data["2026-06-25|heresy|555"]["pairs"] == [["15", "16"]]
    assert "Bushido" in owner.public[-1] and "Horus Heresy" in owner.public[-1]


def test_signup_instructions_explain_defaults_and_replacement_without_truncation():
    for game in ("heresy", "bushido", "40k-3rd", "old-world"):
        text = build_signup_instructions(event=event(game))
        assert "Leave `game` blank" in text
        assert "one game per night" in text
        assert "another event thread" in text
        assert text.endswith("organised play event thread.")
        assert len(text) < 2000
