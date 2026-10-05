from __future__ import annotations

import random
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date
from typing import Any

from bot.services.club_night_signups import (
    SIDE_LABELS,
    normalize_format_pref,
    normalize_side,
)
from bot.services.discord_text import escape_public_text
from bot.services.organised_play import (
    signup_details,
    signup_game,
    group_signups,
    game_label,
)
from bot.services.stadium_events import format_date
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT, truncate


@dataclass(frozen=True)
class Match:
    """One scheduled game between two opposing sides.

    Each side is a tuple of sorted user ids, so a 1v1 is ``side_a``/``side_b`` of
    length one and a doubles game (2v1, 2v2, …) simply has a longer side. The
    two-sided shape is intrinsic to a wargame; the *sizes* are open, so future
    formats (3v3, handicap games) need no structural change to this model.
    """

    side_a: tuple[str, ...]
    side_b: tuple[str, ...]

    def __post_init__(self) -> None:
        normalized_sides: list[tuple[str, ...]] = []
        for side_name, side in (("side_a", self.side_a), ("side_b", self.side_b)):
            if not isinstance(side, Sequence) or isinstance(side, (str, bytes)):
                raise TypeError(f"{side_name} must be a sequence of user ids.")
            players = tuple(side)
            if not players:
                raise ValueError(f"{side_name} must contain at least one player.")
            if any(
                not isinstance(player, str) or not player.strip()
                for player in players
            ):
                raise ValueError(f"{side_name} must contain non-empty string user ids.")
            normalized = tuple(sorted(players))
            if len(normalized) != len(set(normalized)):
                raise ValueError(f"{side_name} cannot contain the same player twice.")
            normalized_sides.append(normalized)

        side_a, side_b = normalized_sides
        if set(side_a) & set(side_b):
            raise ValueError("A player cannot appear on both sides of a match.")

        object.__setattr__(self, "side_a", side_a)
        object.__setattr__(self, "side_b", side_b)

    @property
    def players(self) -> tuple[str, ...]:
        return self.side_a + self.side_b

    @property
    def is_singles(self) -> bool:
        return len(self.side_a) == 1 and len(self.side_b) == 1

    def opponent_pairs(self) -> tuple[frozenset[str], ...]:
        """Every cross-side (opponent) pair in this match.

        Teammates are *not* opponents, so a 2v1 ``(A, B) vs (C)`` yields
        ``{A, C}`` and ``{B, C}`` only. This is what feeds recent-opponent
        avoidance and pairing history, keeping avoidance identical to the 1v1
        era while ignoring who shared a side.
        """
        return tuple(
            frozenset((a, b)) for a in self.side_a for b in self.side_b
        )


@dataclass(frozen=True)
class PairingResult:
    matches: tuple[Match, ...]
    byes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.matches, Sequence) or isinstance(
            self.matches, (str, bytes)
        ):
            raise TypeError("matches must be a sequence of Match instances.")
        if not isinstance(self.byes, Sequence) or isinstance(
            self.byes, (str, bytes)
        ):
            raise TypeError("byes must be a sequence of user ids.")
        matches = tuple(self.matches)
        byes = tuple(self.byes)
        if any(not isinstance(match, Match) for match in matches):
            raise TypeError("matches must contain Match instances.")
        if any(
            not isinstance(player, str) or not player.strip()
            for player in byes
        ):
            raise ValueError("byes must contain non-empty string user ids.")

        participants = [
            player for match in matches for player in match.players
        ] + list(byes)
        if len(participants) != len(set(participants)):
            raise ValueError(
                "A player cannot appear in more than one match or bye."
            )

        object.__setattr__(self, "matches", matches)
        object.__setattr__(self, "byes", byes)

    @property
    def participants(self) -> tuple[str, ...]:
        return tuple(
            player for match in self.matches for player in match.players
        ) + self.byes

    def opponent_pairs(self) -> tuple[frozenset[str], ...]:
        pairs: list[frozenset[str]] = []
        for match in self.matches:
            pairs.extend(match.opponent_pairs())
        return tuple(pairs)


def _make_match(side_a: Sequence[str], side_b: Sequence[str]) -> Match:
    return Match(side_a=tuple(side_a), side_b=tuple(side_b))


def is_mutual_pair(signups: dict[str, dict[str, Any]], a: str, b: str) -> bool:
    return (
        a != b
        and _preference(signups, a) == b
        and _preference(signups, b) == a
    )


def generate_pairings(
    signups: dict[str, dict[str, Any]],
    rng: random.Random | None = None,
    *,
    recent_opponents: AbstractSet[frozenset[str]] = frozenset(),
    locked_pairs: AbstractSet[frozenset[str]] = frozenset(),
    avoided_pairs: AbstractSet[frozenset[str]] = frozenset(),
    forced_bye: str | None = None,
) -> PairingResult:
    """Pair signed-up players for a club night.

    Players are partitioned by their selected game first; locks and preferences
    never pair players across games.

    Priority order: a ``forced_bye`` (if signed up) is set aside first, then
    ``locked_pairs`` are paired regardless of sides, then mutual preferences are
    locked — all of these are 1v1. Everyone left is grouped into *units* (a lone
    player, or a 2-player doubles team) and units are matched across sides.

    **Auto-teaming.** Whether a player may be teamed comes from their sign-up
    ``format_pref``: ``solo`` is never teamed (but may face a team), ``doubles`` is teamed eagerly with
    a compatible partner, and ``either`` is teamed only when it improves the night
    (chiefly to turn an odd-one-out bye into a 2v1). Teammates always share a
    faction side (two Loyalists, two Traitors, or with an "either" player); a
    team then faces an opposing unit, giving a 2v1 against a lone opponent or a
    2v2 against another team. When no compatible teaming is possible the odd
    player out is reported as a bye for the organiser, exactly as before.

    ``recent_opponents`` and ``avoided_pairs`` are soft constraints applied when
    matching units: prefer an opponent unit that shares no recently-played or
    avoided cross-side pair, falling back only when no alternative exists. Locked
    pairs, forced byes, and mutual preferences all outrank them — a forced
    rematch or avoid-override is marked by ``format_pairings`` rather than
    honoured by breaking a higher-priority rule.
    """
    target_rng = rng if rng is not None else random.SystemRandom()
    groups = group_signups(signups)
    if len(groups) > 1:
        results = [
            generate_pairings(
                groups[game],
                rng=target_rng,
                recent_opponents=recent_opponents,
                locked_pairs=locked_pairs,
                avoided_pairs=avoided_pairs,
                forced_bye=forced_bye,
            )
            for game in sorted(groups)
        ]
        return PairingResult(
            matches=tuple(match for result in results for match in result.matches),
            byes=tuple(player for result in results for player in result.byes),
        )
    players = sorted(signups)

    matches: list[Match] = []
    placed: set[str] = set()
    byes: list[str] = []

    if forced_bye is not None and forced_bye in signups:
        byes.append(forced_bye)
        placed.add(forced_bye)

    for a, b in _ordered_pairs(locked_pairs):
        if a not in signups or b not in signups or a in placed or b in placed:
            continue
        matches.append(_make_match((a,), (b,)))
        placed.update((a, b))

    for player in players:
        if player in placed:
            continue
        preferred = _preference(signups, player)
        if (
            preferred is None
            or preferred == player
            or preferred not in signups
            or preferred in placed
        ):
            continue
        if _preference(signups, preferred) == player:
            matches.append(_make_match((player,), (preferred,)))
            placed.update((player, preferred))

    avoid_set = frozenset(avoided_pairs) | frozenset(recent_opponents)

    remaining = [player for player in players if player not in placed]
    units = _form_units(remaining, signups, target_rng)
    unit_matches, leftover_byes = _match_units(
        units, signups, target_rng, avoid_set
    )
    matches.extend(unit_matches)
    byes.extend(leftover_byes)

    return PairingResult(matches=tuple(matches), byes=tuple(byes))


def _ordered_pairs(
    pairs: AbstractSet[frozenset[str]],
) -> list[tuple[str, str]]:
    # Deterministic order so a fixed RNG and constraint set reproduce a pairing.
    ordered: list[tuple[str, str]] = []
    for pair in pairs:
        members = sorted(pair)
        if len(members) == 2:
            ordered.append((members[0], members[1]))
    return sorted(ordered)


def _form_units(
    remaining: list[str],
    signups: dict[str, dict[str, Any]],
    rng: random.Random,
) -> list[tuple[str, ...]]:
    """Group leftover players into 1- or 2-player units for cross-side matching.

    Doubles-willing players (``format_pref`` doubles/either) may be teamed;
    ``solo`` players never are. Teammates are always faction-compatible (same
    side, or one "either"). A final parity guard adds or dissolves a single team
    so the unit count is even where possible — the difference between a 2v1 and a
    lone bye.
    """
    by_side: dict[str, list[str]] = {"loyalist": [], "traitor": [], "either": []}
    for player in remaining:
        by_side[_side(signups, player)].append(player)

    keen = {
        side: [p for p in bucket if _format_pref(signups, p) == "doubles"]
        for side, bucket in by_side.items()
    }
    flex = {
        side: [p for p in bucket if _format_pref(signups, p) == "either"]
        for side, bucket in by_side.items()
    }
    for bucket in (*keen.values(), *flex.values()):
        rng.shuffle(bucket)
    solo = [p for p in remaining if _format_pref(signups, p) == "solo"]

    teams: list[tuple[str, str]] = []

    _form_keen_teams(keen, flex, teams)

    # A lone keen player takes a compatible "either" partner rather than sit 1v1.
    for side in ("loyalist", "traitor", "either"):
        if keen[side]:
            partner = _pull_flex_partner(flex, side)
            if partner is not None:
                teams.append((keen[side].pop(), partner))

    singletons = [
        player
        for side in ("loyalist", "traitor", "either")
        for player in keen[side] + flex[side]
    ] + solo

    if (len(teams) + len(singletons)) % 2 == 1:
        if not _resolve_parity_with_team(teams, singletons, signups):
            _dissolve_parity_team(teams, singletons, signups)

    return [tuple(sorted(team)) for team in teams] + [
        (player,) for player in singletons
    ]


def _form_keen_teams(
    keen: dict[str, list[str]],
    flex: dict[str, list[str]],
    teams: list[tuple[str, str]],
) -> None:
    faction_sides = ("loyalist", "traitor")
    while keen["either"]:
        odd_sides = [side for side in faction_sides if len(keen[side]) % 2 == 1]
        if not odd_sides:
            break
        side = min(
            odd_sides,
            key=lambda value: (
                len(flex[value]) + len(flex["either"]),
                faction_sides.index(value),
            ),
        )
        teams.append((keen[side].pop(), keen["either"].pop()))

    for side in ("loyalist", "traitor", "either"):
        queue = keen[side]
        while len(queue) >= 2:
            teams.append((queue.pop(), queue.pop()))


def _pull_flex_partner(flex: dict[str, list[str]], side: str) -> str | None:
    # A same-faction "either" partner is ideal; otherwise a side-agnostic
    # "either" player can join a Loyalist or Traitor keen player without
    # creating a cross-faction team.
    if flex[side]:
        return flex[side].pop()
    if side in ("loyalist", "traitor") and flex["either"]:
        return flex["either"].pop()
    if side == "either":
        for faction_side in ("loyalist", "traitor"):
            if flex[faction_side]:
                return flex[faction_side].pop()
    return None


def _resolve_parity_with_team(
    teams: list[tuple[str, str]],
    singletons: list[str],
    signups: dict[str, dict[str, Any]],
) -> bool:
    teamable = [p for p in singletons if _format_pref(signups, p) != "solo"]
    best_score = -1
    best_team_index: int | None = None
    best_singletons: tuple[str, str] | None = None
    best_replacements: tuple[tuple[str, str], ...] = ()

    for index, first in enumerate(teamable):
        for second in teamable[index + 1:]:
            if _sides_compatible(signups, first, second):
                score = _keen_count(signups, (first, second))
                if score > best_score:
                    best_score = score
                    best_team_index = None
                    best_singletons = (first, second)
                    best_replacements = ((first, second),)

    for team_index, (first, second) in enumerate(teams):
        for singleton_index, third in enumerate(teamable):
            for fourth in teamable[singleton_index + 1:]:
                replacements = (
                    ((first, third), (second, fourth)),
                    ((first, fourth), (second, third)),
                )
                for replacement_a, replacement_b in replacements:
                    if _sides_compatible(
                        signups, *replacement_a
                    ) and _sides_compatible(signups, *replacement_b):
                        score = _keen_count(signups, (third, fourth))
                        if score > best_score:
                            best_score = score
                            best_team_index = team_index
                            best_singletons = (third, fourth)
                            best_replacements = (replacement_a, replacement_b)

    if best_singletons is None:
        return False
    if best_team_index is None:
        teams.extend(best_replacements)
    else:
        teams[best_team_index:best_team_index + 1] = best_replacements
    singletons.remove(best_singletons[0])
    singletons.remove(best_singletons[1])
    return True


def _keen_count(
    signups: dict[str, dict[str, Any]], players: tuple[str, str]
) -> int:
    return sum(1 for player in players if _format_pref(signups, player) == "doubles")


def _dissolve_parity_team(
    teams: list[tuple[str, str]],
    singletons: list[str],
    signups: dict[str, dict[str, Any]],
) -> None:
    # No pair could be teamed to fix parity, so break one existing team back into
    # 1v1s instead of leaving a bye. Sacrifice the team with the fewest keen
    # (doubles-preferring) members so eager doubles requests survive longest.
    if not teams:
        return
    index = min(
        range(len(teams)),
        key=lambda i: sum(
            1 for p in teams[i] if _format_pref(signups, p) == "doubles"
        ),
    )
    first, second = teams.pop(index)
    singletons.extend((first, second))


def _sides_compatible(
    signups: dict[str, dict[str, Any]], a: str, b: str
) -> bool:
    sides = {_side(signups, a), _side(signups, b)}
    return sides != {"loyalist", "traitor"}


def _match_units(
    units: list[tuple[str, ...]],
    signups: dict[str, dict[str, Any]],
    rng: random.Random,
    avoid_set: AbstractSet[frozenset[str]],
) -> tuple[list[Match], list[str]]:
    loyalists = [u for u in units if _unit_side(signups, u) == "loyalist"]
    traitors = [u for u in units if _unit_side(signups, u) == "traitor"]
    either = [u for u in units if _unit_side(signups, u) == "either"]
    rng.shuffle(loyalists)
    rng.shuffle(traitors)

    matches: list[Match] = []

    while loyalists and traitors:
        loyalist = loyalists.pop()
        traitor = _take_unit_partner(traitors, loyalist, avoid_set)
        matches.append(_make_match(loyalist, traitor))

    leftovers = loyalists + traitors + either
    rng.shuffle(leftovers)
    while len(leftovers) >= 2:
        anchor = leftovers.pop()
        partner = _take_unit_partner(leftovers, anchor, avoid_set)
        matches.append(_make_match(anchor, partner))

    byes = list(leftovers[0]) if leftovers else []
    return matches, byes


def _take_unit_partner(
    candidates: list[tuple[str, ...]],
    anchor: tuple[str, ...],
    avoid_set: AbstractSet[frozenset[str]],
) -> tuple[str, ...]:
    # Scan from the end so an empty avoid_set reproduces list.pop(): for 1v1
    # units the unconstrained pairing stays identical to the pre-doubles code.
    for index in range(len(candidates) - 1, -1, -1):
        if not _units_conflict(anchor, candidates[index], avoid_set):
            return candidates.pop(index)
    return candidates.pop()


def _units_conflict(
    a: tuple[str, ...],
    b: tuple[str, ...],
    avoid_set: AbstractSet[frozenset[str]],
) -> bool:
    return any(frozenset((x, y)) in avoid_set for x in a for y in b)


def _unit_side(signups: dict[str, dict[str, Any]], unit: tuple[str, ...]) -> str:
    sides = {_side(signups, player) for player in unit}
    if sides <= {"loyalist", "either"} and "loyalist" in sides:
        return "loyalist"
    if sides <= {"traitor", "either"} and "traitor" in sides:
        return "traitor"
    return "either"


def format_pairings(
    event_date: date,
    result: PairingResult,
    signups: dict[str, dict[str, Any]],
    *,
    recent_opponents: AbstractSet[frozenset[str]] = frozenset(),
    locked_pairs: AbstractSet[frozenset[str]] = frozenset(),
    avoided_pairs: AbstractSet[frozenset[str]] = frozenset(),
    limit: int = DISCORD_MESSAGE_LIMIT,
) -> str:
    lines = [f"**Club night pairings — {format_date(event_date)}**", ""]

    for match in result.matches:
        marker = ""
        opponent_pairs = match.opponent_pairs()
        if match.is_singles:
            key = frozenset(match.players)
            if key in locked_pairs:
                marker += " *(locked)*"
            if is_mutual_pair(signups, match.side_a[0], match.side_b[0]):
                marker += " *(requested)*"
        if any(pair in recent_opponents for pair in opponent_pairs):
            marker += " *(rematch)*"
        if any(pair in avoided_pairs for pair in opponent_pairs):
            marker += " *(avoid override)*"
        lines.append(
            f"{_render_side(signups, match.side_a)} vs "
            f"{_render_side(signups, match.side_b)}{marker}"
        )

    for bye in result.byes:
        lines.append(f"Bye: {_display(signups, bye)} — organiser to resolve.")

    if any(
        len({signup_game(signups[player]) for player in pair if player in signups}) > 1
        for pair in locked_pairs
    ):
        lines.append("Locks between different games are not applied.")
    count = len(signups)
    player_word = "player" if count == 1 else "players"
    lines.append("")
    lines.append(f"{count} {player_word} signed up.")

    return truncate("\n".join(lines), limit)


def _render_side(signups: dict[str, dict[str, Any]], side: tuple[str, ...]) -> str:
    return " & ".join(_display(signups, player) for player in side)


def _preference(signups: dict[str, dict[str, Any]], user_id: str) -> str | None:
    entry = signups.get(user_id) or {}
    preferred = entry.get("preferred_user_id")
    return preferred if isinstance(preferred, str) else None


def _format_pref(signups: dict[str, dict[str, Any]], user_id: str) -> str:
    entry = signups.get(user_id) or {}
    return (
        normalize_format_pref(entry.get("format_pref"))
        if signup_game(entry) == "heresy"
        else "solo"
    )


def _side(signups: dict[str, dict[str, Any]], user_id: str) -> str:
    entry = signups.get(user_id) or {}
    return (
        normalize_side(entry.get("side"))
        if signup_game(entry) == "heresy"
        else "either"
    )


def _display(signups: dict[str, dict[str, Any]], user_id: str) -> str:
    entry = signups.get(user_id) or {}
    name = escape_public_text(str(entry.get("display_name") or user_id))
    details = signup_details(entry)
    if entry.get("game"):
        details.insert(0, escape_public_text(game_label(signup_game(entry))))
    if details:
        name += " (" + ", ".join(details) + ")"
    label = (
        SIDE_LABELS.get(entry.get("side"))
        if entry.get("game", "heresy") == "heresy"
        else None
    )
    return f"{name} ({label})" if label else name
