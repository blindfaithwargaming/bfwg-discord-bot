import random
from datetime import date

import pytest

from bot.services.club_night_pairings import (
    Match,
    PairingResult,
    format_pairings,
    generate_pairings,
)
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT


EVENT_DATE = date(2026, 6, 25)


def _signup(
    name: str,
    *,
    side: str = "either",
    preferred: str | None = None,
    fmt: str = "solo",
) -> dict:
    return {
        "display_name": name,
        "signed_up_at": "2026-06-12T12:00:00+00:00",
        "side": side,
        "preferred_user_id": preferred,
        "format_pref": fmt,
    }


def _all_players(result: PairingResult) -> list[str]:
    return list(result.participants)


def _opponent_pairs(result: PairingResult) -> set[frozenset[str]]:
    return set(result.opponent_pairs())


# ---------------------------------------------------------------------------
# Match dataclass
# ---------------------------------------------------------------------------

def test_match_players_and_is_singles() -> None:
    singles = Match(side_a=("1",), side_b=("2",))
    doubles = Match(side_a=("1", "2"), side_b=("3",))

    assert singles.is_singles is True
    assert doubles.is_singles is False
    assert sorted(doubles.players) == ["1", "2", "3"]


def test_match_sorts_players_within_sides_without_swapping_sides() -> None:
    match = Match(side_a=("9", "1"), side_b=("4", "2"))

    assert match.side_a == ("1", "9")
    assert match.side_b == ("2", "4")


@pytest.mark.parametrize(
    ("side_a", "side_b", "message"),
    [
        ((), ("2",), "side_a must contain at least one player"),
        (("1",), (), "side_b must contain at least one player"),
        (("1", "1"), ("2",), "side_a cannot contain the same player twice"),
        (("1",), ("1", "2"), "cannot appear on both sides"),
        (("",), ("2",), "non-empty string user ids"),
        ((" ",), ("2",), "non-empty string user ids"),
    ],
)
def test_match_rejects_invalid_states(
    side_a: tuple[str, ...],
    side_b: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        Match(side_a=side_a, side_b=side_b)


def test_pairing_result_rejects_player_in_multiple_places() -> None:
    with pytest.raises(ValueError, match="more than one match or bye"):
        PairingResult(
            matches=(Match(side_a=("1",), side_b=("2",)),),
            byes=("1",),
        )


@pytest.mark.parametrize(
    ("matches", "byes", "message"),
    [
        ("not matches", (), "matches must be a sequence"),
        ((), "12", "byes must be a sequence"),
        ((object(),), (), "matches must contain Match"),
        ((), (1,), "byes must contain non-empty string"),
        ((), (" ",), "byes must contain non-empty string"),
    ],
)
def test_pairing_result_rejects_invalid_container_states(
    matches: object, byes: object, message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        PairingResult(matches=matches, byes=byes)  # type: ignore[arg-type]


def test_match_and_result_reject_unordered_containers() -> None:
    with pytest.raises(TypeError, match="side_a must be a sequence"):
        Match(side_a={"1"}, side_b=("2",))  # type: ignore[arg-type]

    match = Match(side_a=("1",), side_b=("2",))
    with pytest.raises(TypeError, match="matches must be a sequence"):
        PairingResult(matches={match}, byes=())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="byes must be a sequence"):
        PairingResult(matches=(match,), byes={"3"})  # type: ignore[arg-type]


def test_match_opponent_pairs_excludes_teammates() -> None:
    match = Match(side_a=("1", "2"), side_b=("3",))

    assert set(match.opponent_pairs()) == {
        frozenset({"1", "3"}),
        frozenset({"2", "3"}),
    }
    assert frozenset({"1", "2"}) not in match.opponent_pairs()


def test_two_by_two_opponent_pairs_are_the_four_cross_pairs() -> None:
    match = Match(side_a=("1", "2"), side_b=("3", "4"))

    assert set(match.opponent_pairs()) == {
        frozenset({"1", "3"}),
        frozenset({"1", "4"}),
        frozenset({"2", "3"}),
        frozenset({"2", "4"}),
    }


def test_match_supports_larger_sides_without_a_model_change() -> None:
    match = Match(side_a=("3", "1", "2"), side_b=("6", "4", "5"))
    result = PairingResult(matches=(match,), byes=())

    assert match.side_a == ("1", "2", "3")
    assert match.side_b == ("4", "5", "6")
    assert result.participants == ("1", "2", "3", "4", "5", "6")
    assert len(result.opponent_pairs()) == 9


# ---------------------------------------------------------------------------
# Preferences (all 1v1)
# ---------------------------------------------------------------------------

def test_mutual_preference_is_locked() -> None:
    signups = {
        "1": _signup("A", preferred="2"),
        "2": _signup("B", preferred="1"),
        "3": _signup("C"),
        "4": _signup("D"),
    }

    for seed in range(10):
        result = generate_pairings(signups, rng=random.Random(seed))
        assert Match(side_a=("1",), side_b=("2",)) in result.matches
        assert result.byes == ()


def test_one_way_preference_does_not_force_a_pair() -> None:
    signups = {
        "1": _signup("A", preferred="2"),
        "2": _signup("B"),
        "3": _signup("C"),
        "4": _signup("D"),
    }

    paired_with = set()
    for seed in range(20):
        result = generate_pairings(signups, rng=random.Random(seed))
        for pair in _opponent_pairs(result):
            if "1" in pair:
                paired_with.update(pair - {"1"})

    assert paired_with != {"2"}


def test_preference_for_non_signed_up_user_is_ignored() -> None:
    signups = {
        "1": _signup("A", preferred="999"),
        "2": _signup("B"),
    }

    result = generate_pairings(signups, rng=random.Random(0))

    assert result.matches == (Match(side_a=("2",), side_b=("1",)),)
    assert result.byes == ()


def test_self_preference_is_ignored() -> None:
    signups = {
        "1": _signup("A", preferred="1"),
        "2": _signup("B", preferred="1"),
    }

    result = generate_pairings(signups, rng=random.Random(0))

    assert len(result.matches) == 1
    assert result.byes == ()


# ---------------------------------------------------------------------------
# Faction-side matching (all 1v1 by default)
# ---------------------------------------------------------------------------

def test_loyalists_matched_against_traitors() -> None:
    signups = {
        "1": _signup("A", side="loyalist"),
        "2": _signup("B", side="loyalist"),
        "3": _signup("C", side="traitor"),
        "4": _signup("D", side="traitor"),
    }

    for seed in range(20):
        result = generate_pairings(signups, rng=random.Random(seed))
        for match in result.matches:
            sides = {signups[p]["side"] for p in match.players}
            assert sides == {"loyalist", "traitor"}


def test_either_players_fill_in_after_cross_side_matching() -> None:
    signups = {
        "1": _signup("A", side="loyalist"),
        "2": _signup("B", side="traitor"),
        "3": _signup("C", side="either"),
        "4": _signup("D", side="either"),
    }

    for seed in range(20):
        result = generate_pairings(signups, rng=random.Random(seed))
        pairs = _opponent_pairs(result)
        assert any(
            {signups[a]["side"], signups[b]["side"]} == {"loyalist", "traitor"}
            for a, b in (tuple(p) for p in pairs)
        )
        assert frozenset({"3", "4"}) in pairs


def test_surplus_side_pairs_internally() -> None:
    signups = {
        "1": _signup("A", side="loyalist"),
        "2": _signup("B", side="loyalist"),
        "3": _signup("C", side="loyalist"),
        "4": _signup("D", side="traitor"),
    }

    result = generate_pairings(signups, rng=random.Random(0))

    cross = [
        pair
        for pair in _opponent_pairs(result)
        if {signups[a]["side"] for a in pair} == {"loyalist", "traitor"}
    ]
    assert len(cross) == 1
    assert len(result.matches) == 2
    assert result.byes == ()


def test_mutual_preference_overrides_side_matching() -> None:
    signups = {
        "1": _signup("A", side="loyalist", preferred="2"),
        "2": _signup("B", side="loyalist", preferred="1"),
        "3": _signup("C", side="traitor"),
        "4": _signup("D", side="traitor"),
    }

    result = generate_pairings(signups, rng=random.Random(0))

    assert Match(side_a=("1",), side_b=("2",)) in result.matches


# ---------------------------------------------------------------------------
# Byes and coverage
# ---------------------------------------------------------------------------

def test_odd_count_yields_bye_and_even_count_does_not() -> None:
    odd = {str(i): _signup(f"P{i}") for i in range(1, 6)}
    even = {str(i): _signup(f"P{i}") for i in range(1, 5)}

    odd_result = generate_pairings(odd, rng=random.Random(0))
    even_result = generate_pairings(even, rng=random.Random(0))

    assert len(odd_result.byes) == 1
    assert len(odd_result.matches) == 2
    assert even_result.byes == ()
    assert len(even_result.matches) == 2


def test_zero_and_one_signups() -> None:
    empty = generate_pairings({}, rng=random.Random(0))
    solo = generate_pairings({"1": _signup("A")}, rng=random.Random(0))

    assert empty.matches == ()
    assert empty.byes == ()
    assert solo.matches == ()
    assert solo.byes == ("1",)


def test_every_player_appears_exactly_once() -> None:
    sides = ["loyalist", "traitor", "either"]
    signups = {
        str(i): _signup(
            f"P{i}",
            side=sides[i % 3],
            preferred=str(i + 1) if i % 7 == 0 else None,
        )
        for i in range(1, 42)
    }

    for seed in range(10):
        result = generate_pairings(signups, rng=random.Random(seed))
        players = _all_players(result)
        assert sorted(players) == sorted(signups)
        assert len(players) == len(set(players))


def test_seeded_rng_is_deterministic() -> None:
    signups = {str(i): _signup(f"P{i}") for i in range(1, 11)}

    first = generate_pairings(signups, rng=random.Random(7))
    second = generate_pairings(signups, rng=random.Random(7))

    assert first == second


def test_all_solo_pairings_preserve_legacy_rng_sequence_and_side_order() -> None:
    signups = {
        "1": _signup("L1", side="loyalist"),
        "2": _signup("T2", side="traitor"),
        "3": _signup("E3"),
        "4": _signup("T4", side="traitor"),
        "5": _signup("L5", side="loyalist"),
        "6": _signup("E6"),
    }

    result = generate_pairings(signups, rng=random.Random(17))

    assert result == PairingResult(
        matches=(
            Match(side_a=("5",), side_b=("4",)),
            Match(side_a=("1",), side_b=("2",)),
            Match(side_a=("6",), side_b=("3",)),
        ),
        byes=(),
    )


# ---------------------------------------------------------------------------
# Auto-teaming (doubles)
# ---------------------------------------------------------------------------

def test_solo_default_is_never_teamed() -> None:
    signups = {str(i): _signup(f"P{i}") for i in range(1, 8)}

    for seed in range(10):
        result = generate_pairings(signups, rng=random.Random(seed))
        assert all(match.is_singles for match in result.matches)


def test_four_doubles_players_form_one_two_by_two() -> None:
    signups = {str(i): _signup(f"P{i}", fmt="doubles") for i in range(1, 5)}

    for seed in range(10):
        result = generate_pairings(signups, rng=random.Random(seed))
        assert len(result.matches) == 1
        match = result.matches[0]
        assert len(match.side_a) == 2 and len(match.side_b) == 2
        assert result.byes == ()


def test_two_doubles_and_a_solo_make_a_two_by_one() -> None:
    signups = {
        "1": _signup("A", side="loyalist", fmt="doubles"),
        "2": _signup("B", side="loyalist", fmt="doubles"),
        "3": _signup("C", side="traitor", fmt="solo"),
    }

    result = generate_pairings(signups, rng=random.Random(0))

    assert result.matches == (Match(side_a=("1", "2"), side_b=("3",)),)
    assert result.byes == ()


def test_explicit_doubles_players_are_teamed_before_flexible_players() -> None:
    signups = {
        "1": _signup("A", side="loyalist", fmt="doubles"),
        "2": _signup("B", side="loyalist", fmt="either"),
        "3": _signup("C", side="either", fmt="doubles"),
    }

    result = generate_pairings(signups, rng=random.Random(0))

    assert len(result.matches) == 1
    assert {"1", "3"} in (
        set(result.matches[0].side_a),
        set(result.matches[0].side_b),
    )
    assert result.byes == ()


def test_teams_rebalance_before_dissolving_for_parity() -> None:
    signups = {
        "1": _signup("A", side="loyalist", fmt="doubles"),
        "2": _signup("B", side="loyalist", fmt="either"),
        "3": _signup("C", side="traitor", fmt="doubles"),
        "4": _signup("D", side="either", fmt="doubles"),
    }

    result = generate_pairings(signups, rng=random.Random(0))

    assert result.byes == ()
    assert len(result.matches) == 1
    assert len(result.matches[0].side_a) == 2
    assert len(result.matches[0].side_b) == 2
    assert all(
        any(
            player in side
            for side in (result.matches[0].side_a, result.matches[0].side_b)
        )
        for player in ("1", "3", "4")
    )


def test_parity_rebalance_prioritises_unteamed_doubles_over_flexible_pair() -> None:
    signups = {
        "1": _signup("A", side="loyalist", fmt="doubles"),
        "2": _signup("B", side="loyalist", fmt="either"),
        "3": _signup("C", side="loyalist", fmt="either"),
        "4": _signup("D", side="loyalist", fmt="either"),
        "5": _signup("E", side="traitor", fmt="doubles"),
        "6": _signup("F", side="either", fmt="doubles"),
    }

    result = generate_pairings(signups, rng=random.Random(0))

    teamed = {
        player
        for match in result.matches
        for side in (match.side_a, match.side_b)
        if len(side) > 1
        for player in side
    }
    assert {"1", "5", "6"} <= teamed
    assert result.byes == ()


def test_either_players_team_up_to_avoid_a_bye() -> None:
    signups = {str(i): _signup(f"P{i}", fmt="either") for i in range(1, 6)}

    for seed in range(10):
        result = generate_pairings(signups, rng=random.Random(seed))
        assert result.byes == ()
        assert sorted(_all_players(result)) == sorted(signups)
        assert sum(1 for m in result.matches if not m.is_singles) == 1


def test_bye_avoidance_wins_over_honouring_doubles() -> None:
    # Two keen doubles players plus two solos: teaming the keen pair would strand
    # a solo on a bye, so the team is dissolved back to 1v1s and everyone plays.
    signups = {
        "1": _signup("A", side="loyalist", fmt="doubles"),
        "2": _signup("B", side="loyalist", fmt="doubles"),
        "3": _signup("C", side="traitor", fmt="solo"),
        "4": _signup("D", side="traitor", fmt="solo"),
    }

    result = generate_pairings(signups, rng=random.Random(0))

    assert result.byes == ()
    assert all(match.is_singles for match in result.matches)
    assert sorted(_all_players(result)) == sorted(signups)


def test_teammates_always_share_a_faction_side() -> None:
    sides = ["loyalist", "traitor", "either"]
    formats = ["doubles", "either", "solo"]
    signups = {
        str(i): _signup(f"P{i}", side=sides[i % 3], fmt=formats[i % 3])
        for i in range(1, 40)
    }

    for seed in range(10):
        result = generate_pairings(signups, rng=random.Random(seed))
        for match in result.matches:
            for side in (match.side_a, match.side_b):
                team_sides = {signups[p]["side"] for p in side}
                assert team_sides != {"loyalist", "traitor"}


def test_solo_player_is_never_pulled_into_a_team() -> None:
    signups = {
        "1": _signup("A", fmt="doubles"),
        "2": _signup("B", fmt="doubles"),
        "3": _signup("C", fmt="doubles"),
        "4": _signup("D", fmt="solo"),
        "5": _signup("E", fmt="either"),
    }

    for seed in range(15):
        result = generate_pairings(signups, rng=random.Random(seed))
        for match in result.matches:
            for side in (match.side_a, match.side_b):
                if len(side) > 1:
                    assert "4" not in side


def test_every_player_appears_once_with_doubles_active() -> None:
    sides = ["loyalist", "traitor", "either"]
    formats = ["doubles", "either", "doubles", "solo"]
    signups = {
        str(i): _signup(f"P{i}", side=sides[i % 3], fmt=formats[i % 4])
        for i in range(1, 43)
    }

    for seed in range(10):
        result = generate_pairings(signups, rng=random.Random(seed))
        players = _all_players(result)
        assert sorted(players) == sorted(signups)
        assert len(players) == len(set(players))


# ---------------------------------------------------------------------------
# Recent-opponent avoidance
# ---------------------------------------------------------------------------

def test_recent_opponents_split_in_side_matching_stage() -> None:
    signups = {
        "1": _signup("A", side="loyalist"),
        "2": _signup("B", side="loyalist"),
        "3": _signup("C", side="traitor"),
        "4": _signup("D", side="traitor"),
    }
    recent = frozenset({frozenset({"1", "3"})})

    # Soft avoidance: when a same-stage alternative exists it is taken. This is
    # best-effort per anchor (as documented), so it is asserted on a representative
    # seed rather than universally.
    result = generate_pairings(signups, rng=random.Random(1), recent_opponents=recent)

    assert frozenset({"1", "3"}) not in _opponent_pairs(result)


def test_recent_opponents_split_in_random_leftover_stage() -> None:
    signups = {str(i): _signup(chr(64 + i)) for i in range(1, 5)}
    recent = frozenset({frozenset({"1", "2"})})

    result = generate_pairings(signups, rng=random.Random(1), recent_opponents=recent)

    assert frozenset({"1", "2"}) not in _opponent_pairs(result)


def test_mutual_preference_pairs_recent_opponents_anyway() -> None:
    signups = {
        "1": _signup("A", preferred="2"),
        "2": _signup("B", preferred="1"),
        "3": _signup("C"),
        "4": _signup("D"),
    }
    recent = frozenset({frozenset({"1", "2"})})

    result = generate_pairings(signups, rng=random.Random(0), recent_opponents=recent)

    assert Match(side_a=("1",), side_b=("2",)) in result.matches


def test_unavoidable_rematch_is_made_and_marked() -> None:
    signups = {"1": _signup("Aaron"), "2": _signup("Dave")}
    recent = frozenset({frozenset({"1", "2"})})

    result = generate_pairings(signups, rng=random.Random(0), recent_opponents=recent)
    text = format_pairings(EVENT_DATE, result, signups, recent_opponents=recent)

    assert len(result.matches) == 1
    assert _opponent_pairs(result) == {frozenset({"1", "2"})}
    assert "*(rematch)*" in text


def test_side_matching_not_broken_to_avoid_rematch() -> None:
    signups = {
        "1": _signup("A", side="loyalist"),
        "2": _signup("B", side="traitor"),
        "3": _signup("C", side="either"),
        "4": _signup("D", side="either"),
    }
    recent = frozenset({frozenset({"1", "2"})})

    for seed in range(6):
        result = generate_pairings(
            signups, rng=random.Random(seed), recent_opponents=recent
        )
        assert frozenset({"1", "2"}) in _opponent_pairs(result)


def test_empty_recent_opponents_matches_call_without_parameter() -> None:
    signups = {
        str(i): _signup(f"P{i}", side=["loyalist", "traitor", "either"][i % 3])
        for i in range(1, 13)
    }

    without = generate_pairings(signups, rng=random.Random(3))
    with_empty = generate_pairings(
        signups, rng=random.Random(3), recent_opponents=frozenset()
    )

    assert without == with_empty


def test_every_player_appears_once_with_avoidance_active() -> None:
    sides = ["loyalist", "traitor", "either"]
    signups = {
        str(i): _signup(f"P{i}", side=sides[i % 3])
        for i in range(1, 42)
    }
    recent = frozenset(
        {frozenset({str(i), str(i + 1)}) for i in range(1, 41, 2)}
    )

    for seed in range(10):
        result = generate_pairings(
            signups, rng=random.Random(seed), recent_opponents=recent
        )
        players = _all_players(result)
        assert sorted(players) == sorted(signups)
        assert len(players) == len(set(players))


def test_team_opponent_avoidance_is_respected() -> None:
    # A doubles team facing a lone opponent should avoid a recently-played
    # cross-side pair when an alternative opponent exists.
    signups = {
        "1": _signup("A", side="loyalist", fmt="doubles"),
        "2": _signup("B", side="loyalist", fmt="doubles"),
        "3": _signup("C", side="traitor", fmt="solo"),
        "4": _signup("D", side="traitor", fmt="solo"),
        "5": _signup("E", side="traitor", fmt="solo"),
    }
    recent = frozenset({frozenset({"1", "3"}), frozenset({"2", "3"})})

    for seed in range(8):
        result = generate_pairings(
            signups, rng=random.Random(seed), recent_opponents=recent
        )
        team_match = next(m for m in result.matches if not m.is_singles)
        assert set(team_match.side_a) == {"1", "2"}
        assert team_match.side_b != ("3",)


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------

def test_locked_pair_wins_over_sides() -> None:
    signups = {
        "1": _signup("A", side="loyalist"),
        "2": _signup("B", side="loyalist"),
        "3": _signup("C", side="traitor"),
        "4": _signup("D", side="traitor"),
    }
    locks = frozenset({frozenset({"1", "2"})})

    for seed in range(10):
        result = generate_pairings(
            signups, rng=random.Random(seed), locked_pairs=locks
        )
        assert Match(side_a=("1",), side_b=("2",)) in result.matches


def test_locked_pair_outranks_mutual_preference() -> None:
    signups = {
        "1": _signup("A", preferred="2"),
        "2": _signup("B", preferred="1"),
        "3": _signup("C"),
        "4": _signup("D"),
    }
    locks = frozenset({frozenset({"1", "3"})})

    result = generate_pairings(signups, rng=random.Random(0), locked_pairs=locks)

    assert Match(side_a=("1",), side_b=("3",)) in result.matches
    assert frozenset({"1", "2"}) not in _opponent_pairs(result)


def test_conflicting_and_absent_locks_are_skipped() -> None:
    signups = {str(i): _signup(f"P{i}") for i in range(1, 5)}
    locks = frozenset(
        {
            frozenset({"1", "2"}),
            frozenset({"2", "3"}),  # conflicts: 2 already locked to 1
            frozenset({"1", "999"}),  # absent player
        }
    )

    result = generate_pairings(signups, rng=random.Random(0), locked_pairs=locks)

    assert Match(side_a=("1",), side_b=("2",)) in result.matches
    assert sorted(_all_players(result)) == sorted(signups)


# ---------------------------------------------------------------------------
# Avoids
# ---------------------------------------------------------------------------

def test_avoided_pair_is_honoured_in_side_matching_stage() -> None:
    signups = {
        "1": _signup("A", side="loyalist"),
        "2": _signup("B", side="loyalist"),
        "3": _signup("C", side="traitor"),
        "4": _signup("D", side="traitor"),
    }
    avoids = frozenset({frozenset({"1", "3"})})

    result = generate_pairings(signups, rng=random.Random(1), avoided_pairs=avoids)

    assert frozenset({"1", "3"}) not in _opponent_pairs(result)


def test_unavoidable_avoid_falls_back_and_is_marked() -> None:
    signups = {"1": _signup("Aaron"), "2": _signup("Dave")}
    avoids = frozenset({frozenset({"1", "2"})})

    result = generate_pairings(signups, rng=random.Random(0), avoided_pairs=avoids)
    text = format_pairings(EVENT_DATE, result, signups, avoided_pairs=avoids)

    assert _opponent_pairs(result) == {frozenset({"1", "2"})}
    assert "*(avoid override)*" in text


# ---------------------------------------------------------------------------
# Forced byes
# ---------------------------------------------------------------------------

def test_forced_bye_on_even_pool_yields_two_byes() -> None:
    signups = {str(i): _signup(f"P{i}") for i in range(1, 5)}

    result = generate_pairings(signups, rng=random.Random(0), forced_bye="1")

    assert "1" in result.byes
    assert len(result.byes) == 2
    assert len(result.matches) == 1
    assert sorted(_all_players(result)) == sorted(signups)


def test_forced_bye_on_odd_pool_yields_single_bye() -> None:
    signups = {str(i): _signup(f"P{i}") for i in range(1, 6)}

    result = generate_pairings(signups, rng=random.Random(0), forced_bye="3")

    assert result.byes == ("3",)
    assert len(result.matches) == 2


def test_forced_bye_not_signed_up_is_ignored() -> None:
    signups = {str(i): _signup(f"P{i}") for i in range(1, 5)}

    result = generate_pairings(signups, rng=random.Random(0), forced_bye="999")

    assert result.byes == ()
    assert len(result.matches) == 2


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def test_format_pairings_renders_matches_bye_and_count() -> None:
    signups = {
        "1": _signup("Aaron", side="loyalist", preferred="2"),
        "2": _signup("Dave", side="traitor", preferred="1"),
        "3": _signup("Cei"),
    }
    result = PairingResult(
        matches=(Match(side_a=("1",), side_b=("2",)),), byes=("3",)
    )

    text = format_pairings(EVENT_DATE, result, signups)

    assert text.startswith("**Club night pairings — Thursday 25 June 2026**")
    assert "Aaron (Loyalist) vs Dave (Traitor) *(requested)*" in text
    assert "Bye: Cei — organiser to resolve." in text
    assert "3 players signed up." in text


def test_format_pairings_renders_a_doubles_team() -> None:
    signups = {
        "1": _signup("Aaron", side="loyalist"),
        "2": _signup("Bran", side="loyalist"),
        "3": _signup("Cei", side="traitor"),
    }
    result = PairingResult(
        matches=(Match(side_a=("1", "2"), side_b=("3",)),), byes=()
    )

    text = format_pairings(EVENT_DATE, result, signups)

    assert "Aaron (Loyalist) & Bran (Loyalist) vs Cei (Traitor)" in text


def test_format_pairings_escapes_malicious_display_names() -> None:
    signups = {
        "1": _signup("@everyone"),
        "2": _signup("**bold** _sneaky_"),
    }
    result = PairingResult(matches=(Match(side_a=("1",), side_b=("2",)),), byes=())

    text = format_pairings(EVENT_DATE, result, signups)

    assert "@everyone" not in text
    assert "**bold**" not in text


def test_format_pairings_stays_under_discord_limit_with_many_signups() -> None:
    signups = {
        str(i): _signup(f"A long club member display name number {i}")
        for i in range(1, 41)
    }
    result = generate_pairings(signups, rng=random.Random(0))

    text = format_pairings(EVENT_DATE, result, signups)

    assert len(text) <= DISCORD_MESSAGE_LIMIT


def test_format_pairings_respects_custom_limit() -> None:
    signups = {
        str(i): _signup(f"A long club member display name number {i}")
        for i in range(1, 41)
    }
    result = generate_pairings(signups, rng=random.Random(0))

    text = format_pairings(EVENT_DATE, result, signups, limit=500)

    assert len(text) <= 500


def test_requested_and_rematch_markers_both_shown() -> None:
    signups = {
        "1": _signup("Aaron", preferred="2"),
        "2": _signup("Dave", preferred="1"),
        "3": _signup("Cei"),
        "4": _signup("Emrys"),
    }
    recent = frozenset({frozenset({"1", "2"})})

    result = generate_pairings(signups, rng=random.Random(0), recent_opponents=recent)
    text = format_pairings(EVENT_DATE, result, signups, recent_opponents=recent)

    assert "Aaron vs Dave *(requested)* *(rematch)*" in text


def test_format_pairings_renders_locked_marker() -> None:
    signups = {
        "1": _signup("Aaron", side="loyalist"),
        "2": _signup("Dave", side="loyalist"),
    }
    locks = frozenset({frozenset({"1", "2"})})
    result = generate_pairings(signups, rng=random.Random(0), locked_pairs=locks)

    text = format_pairings(EVENT_DATE, result, signups, locked_pairs=locks)

    assert "*(locked)*" in text


# ---------------------------------------------------------------------------
# Data-model invariants (randomised)
#
# The example tests above pin specific behaviours; this pins the *contract* the
# rest of the model relies on, across the whole doubles/side/constraint space.
# If a future change to unit formation or matching breaks one of these
# invariants for some input the example tests happen not to cover, this fails.
# ---------------------------------------------------------------------------

_SIDES = ("loyalist", "traitor", "either")
_FORMATS = ("solo", "doubles", "either")


def _random_signups(rng: random.Random) -> dict[str, dict]:
    return {
        str(i): _signup(
            f"P{i}",
            side=rng.choice(_SIDES),
            fmt=rng.choice(_FORMATS),
        )
        for i in range(rng.randint(0, 9))
    }


@pytest.mark.parametrize("trial", range(200))
def test_generate_pairings_holds_model_invariants(trial: int) -> None:
    rng = random.Random(trial)
    signups = _random_signups(rng)
    ids = list(signups)

    def _maybe_pair() -> frozenset[frozenset[str]]:
        if len(ids) >= 2 and rng.random() < 0.3:
            return frozenset({frozenset(rng.sample(ids, 2))})
        return frozenset()

    locked = _maybe_pair()
    avoided = _maybe_pair()
    recent = _maybe_pair()
    forced_bye = rng.choice(ids) if ids and rng.random() < 0.2 else None

    result = generate_pairings(
        signups,
        rng=rng,
        recent_opponents=recent,
        locked_pairs=locked,
        avoided_pairs=avoided,
        forced_bye=forced_bye,
    )

    participants = list(result.participants)
    # Every signed-up member is placed exactly once, across matches and byes.
    assert sorted(participants) == sorted(signups)
    assert len(participants) == len(set(participants))

    for match in result.matches:
        for side in (match.side_a, match.side_b):
            member_sides = {signups[player]["side"] for player in side}
            # Teammates never straddle the Loyalist/Traitor divide.
            assert member_sides != {"loyalist", "traitor"}
            if len(side) > 1:
                # A ``solo`` member is never folded into a team.
                assert all(
                    signups[player]["format_pref"] != "solo" for player in side
                )

    # Without a forced bye, at most one player is ever left out, and an even
    # pool is always fully paired. An *odd* pool need not yield a bye — doubles
    # teaming can absorb the odd one into a 2v1 — so the count is 0 or 1, not a
    # function of headcount parity the way it was in the 1v1-only era.
    if forced_bye is None:
        assert len(result.byes) <= 1
        if len(signups) % 2 == 0:
            assert not result.byes
