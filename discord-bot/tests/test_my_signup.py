from datetime import date

import pytest

from bot.services.club_night_signups import format_my_signup
from bot.services.stadium_events import ClubNight

EVENT = ClubNight(title="Organised Play - 25th June", event_date=date(2026, 6, 25), thread_id="555")


def test_legacy_defaults_are_explicit():
    text = format_my_signup(event=EVENT, entry={})
    for expected in ("Game: Horus Heresy", "Faction: Not selected", "Play style: Not selected", "Side: Either", "Format: Solo (no teammate)", "Preferred opponent: None"):
        assert expected in text


def test_selected_bushido_game_and_options():
    text = format_my_signup(event=EVENT, entry={"game": "bushido", "faction": "Ito Clan", "play_style": "learning", "rice": 50})
    for expected in ("Game: Bushido", "Faction: Ito Clan", "Play style: Learning", "Rice: 50"):
        assert expected in text
    assert "Side:" not in text
    assert "Format:" not in text


@pytest.mark.parametrize("game", ["40k-3rd", "old-world"])
def test_other_games_omit_heresy_and_bushido_options(game):
    text = format_my_signup(event=EVENT, entry={"game": game})
    assert "Side:" not in text
    assert "Format:" not in text
    assert "Rice:" not in text


def test_bushido_unselected_rice():
    assert "Rice: Not selected" in format_my_signup(event=EVENT, entry={"game": "bushido"})


def test_text_is_escaped_and_bounded():
    text = format_my_signup(event=EVENT, entry={"faction": "@everyone **test**"})
    assert "@everyone" not in text
    assert "**test**" not in text
    assert len(format_my_signup(event=EVENT, entry={"faction": "x" * 3000})) <= 2000
