import asyncio
from pathlib import Path
from types import SimpleNamespace

from bot.commands import mission
from bot.commands.mission import MissionRerollView


def _write_deployment(directory: Path, slug: str, title: str) -> None:
    (directory / f"{slug}.md").write_text(
        "---\n"
        f"name: {slug}\n"
        "source_editions: [heresy3]\n"
        "titan_mission: false\n"
        "points_minimum: null\n"
        "faction_tags: []\n"
        "---\n\n"
        f"# {title}\n\n"
        "Deploy within 12\" of your long table edge.\n",
        encoding="utf-8",
    )


def _write_primary(directory: Path, slug: str, title: str) -> None:
    (directory / f"{slug}.md").write_text(
        "---\n"
        f"name: {slug}\n"
        "source_editions: [heresy3]\n"
        "titan_mission: false\n"
        "points_minimum: null\n"
        "faction_tags: []\n"
        "---\n\n"
        f"# {title}\n\n"
        "One central objective worth 3VP.\n",
        encoding="utf-8",
    )


def _write_secondary(directory: Path, slug: str, title: str) -> None:
    (directory / f"{slug}.md").write_text(
        "---\n"
        f"name: {slug}\n"
        "source_editions: [heresy3]\n"
        "titan_mission: false\n"
        "points_minimum: null\n"
        "faction_tags: []\n"
        "---\n\n"
        f"# {title}\n\n"
        "Score this by doing the thing.\n",
        encoding="utf-8",
    )


def _build_missions_dir(tmp_path: Path) -> Path:
    root = tmp_path / "summaries"
    missions = root / "missions"

    deployments = missions / "deployment-zones"
    deployments.mkdir(parents=True)
    _write_deployment(deployments, "dawn-of-war", "Dawn of War")

    primaries = missions / "primary-objectives"
    primaries.mkdir(parents=True)
    _write_primary(primaries, "the-heart-of-battle", "The Heart of Battle")

    secondaries = missions / "secondary-objectives"
    secondaries.mkdir(parents=True)
    _write_secondary(secondaries, "slay-the-warlord", "Slay the Warlord")

    return root


def _make_reroll_view(owner_id: int = 42) -> MissionRerollView:
    return MissionRerollView(
        owner_user_id=owner_id,
        deployment="dawn-of-war",
        titans=True,
        twist=False,
        max_secondary_vp=2,
    )


def test_mission_reroll_view_stores_owner_and_options() -> None:
    view = _make_reroll_view(owner_id=99)

    assert view._owner_user_id == 99
    assert view._deployment == "dawn-of-war"
    assert view._titans is True
    assert view._twist is False
    assert view._max_secondary_vp == 2


def test_mission_reroll_view_rejects_non_owner() -> None:
    view = _make_reroll_view(owner_id=42)
    sent: list[str] = []
    ephemerals: list[bool] = []

    async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)
        ephemerals.append(ephemeral)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=99),
        response=SimpleNamespace(send_message=fake_send_message),
    )

    asyncio.run(view.reroll_button.callback(interaction))

    assert view._counter == 0
    assert len(sent) == 1
    assert "only the user" in sent[0].lower()
    assert ephemerals == [True]


def test_mission_reroll_view_allows_owner_and_edits_message(
    tmp_path: Path,
    monkeypatch,
) -> None:
    summaries_dir = _build_missions_dir(tmp_path)
    monkeypatch.setattr(
        mission,
        "settings",
        SimpleNamespace(summaries_dir=str(summaries_dir)),
    )

    view = _make_reroll_view(owner_id=42)
    sent: list[str] = []
    edited: list[dict[str, object]] = []

    async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)

    async def fake_edit_message(**kwargs: object) -> None:
        edited.append(kwargs)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        response=SimpleNamespace(
            send_message=fake_send_message,
            edit_message=fake_edit_message,
        ),
    )

    asyncio.run(view.reroll_button.callback(interaction))

    assert view._counter == 1
    assert sent == []
    assert len(edited) == 1
    assert "**The Heart of Battle**" in edited[0]["content"]
    assert edited[0]["allowed_mentions"] is mission.NO_MENTIONS


def test_mission_reroll_view_generation_error_is_ephemeral(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        mission,
        "settings",
        SimpleNamespace(summaries_dir=str(tmp_path / "missing")),
    )

    view = _make_reroll_view(owner_id=42)
    sent: list[str] = []
    ephemerals: list[bool] = []
    edited: list[dict[str, object]] = []

    async def fake_send_message(msg: str, *, ephemeral: bool = True) -> None:
        sent.append(msg)
        ephemerals.append(ephemeral)

    async def fake_edit_message(**kwargs: object) -> None:
        edited.append(kwargs)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        response=SimpleNamespace(
            send_message=fake_send_message,
            edit_message=fake_edit_message,
        ),
    )

    asyncio.run(view.reroll_button.callback(interaction))

    assert len(sent) == 1
    assert "does not exist" in sent[0]
    assert ephemerals == [True]
    assert edited == []
