import asyncio
from pathlib import Path
from types import SimpleNamespace

from bot.commands import rules
from bot.services.rules_query import RulesQueryHit


class _CapturingTree:
    def __init__(self) -> None:
        self.commands: list[object] = []

    def add_command(self, command: object) -> None:
        self.commands.append(command)


class _Response:
    def __init__(self) -> None:
        self._done = False
        self.deferred: list[bool] = []
        self.sent: list[tuple[str, bool]] = []

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self.deferred.append(ephemeral)
        self._done = True

    async def send_message(self, msg: str, *, ephemeral: bool = False) -> None:
        self.sent.append((msg, ephemeral))
        self._done = True


class _Followup:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send(
        self,
        content: str,
        *,
        ephemeral: bool = False,
        allowed_mentions: object = None,
        view: object = None,
        wait: bool = False,
    ) -> object:
        record = {
            "content": content,
            "ephemeral": ephemeral,
            "allowed_mentions": allowed_mentions,
            "view": view,
            "wait": wait,
        }
        self.sent.append(record)
        return SimpleNamespace(record=record)


def _make_interaction(user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        guild_id=1,
        channel_id=2,
        user=SimpleNamespace(id=user_id),
        response=_Response(),
        followup=_Followup(),
    )


def _query_callback():
    tree = _CapturingTree()
    rules.register_rules_commands(tree)
    group = tree.commands[0]
    return group.get_command("query").callback


def _build_rules_dir(tmp_path: Path) -> Path:
    summaries_dir = tmp_path / "summaries"
    rules_dir = summaries_dir / "rules" / "principles-of-combat"
    rules_dir.mkdir(parents=True)
    (rules_dir / "cover.md").write_text(
        "---\n"
        "name: cover\n"
        "rules_topic: Key Actions\n"
        "source_book: principles-of-combat\n"
        "source_url: https://wahapedia.ru/heresy3/the-rules/principles-of-combat/\n"
        "source_section: Key Actions\n"
        "keywords: [cover, save, saving throw]\n"
        "---\n\n"
        "# Cover and Invulnerable Tests\n\n"
        "A model in cover improves its saving throw by one against ranged attacks.\n",
        encoding="utf-8",
    )
    return summaries_dir


def _build_multi_rules_dir(tmp_path: Path) -> Path:
    summaries_dir = _build_rules_dir(tmp_path)
    rules_dir = summaries_dir / "rules" / "principles-of-combat"
    (rules_dir / "saving-throws.md").write_text(
        "---\n"
        "name: saving-throws\n"
        "rules_topic: Key Actions\n"
        "source_book: principles-of-combat\n"
        "source_url: https://wahapedia.ru/heresy3/the-rules/principles-of-combat/\n"
        "source_section: Key Actions\n"
        "keywords: [cover, save, saving throw]\n"
        "---\n\n"
        "# Saving Throws\n\n"
        "Roll a saving throw against each wound; a cover save may apply.\n",
        encoding="utf-8",
    )
    return summaries_dir


def _make_hits() -> list[RulesQueryHit]:
    return [
        RulesQueryHit(
            path="principles-of-combat/cover.md",
            title="Cover and Invulnerable Tests",
            excerpt="A model in cover improves its saving throw.",
            score=20,
            source_book="principles-of-combat",
            source_section="Key Actions",
            source_url="https://wahapedia.ru/heresy3/the-rules/principles-of-combat/",
        ),
        RulesQueryHit(
            path="principles-of-combat/saving-throws.md",
            title="Saving Throws",
            excerpt="Roll a saving throw against each wound.",
            score=10,
            source_book="principles-of-combat",
            source_section="Key Actions",
            source_url="https://wahapedia.ru/heresy3/the-rules/principles-of-combat/",
        ),
    ]


def _make_component_interaction(user_id: int = 42):
    edits: list[dict[str, object]] = []
    rejects: list[tuple[str, bool]] = []

    async def fake_edit_message(*, content=None, view=None, allowed_mentions=None) -> None:
        edits.append({"content": content, "view": view, "allowed_mentions": allowed_mentions})

    async def fake_send_message(msg: str, *, ephemeral: bool = False) -> None:
        rejects.append((msg, ephemeral))

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            edit_message=fake_edit_message,
            send_message=fake_send_message,
        ),
    )
    return interaction, edits, rejects


def test_rules_query_multiple_hits_attaches_select_view(tmp_path: Path, monkeypatch) -> None:
    summaries_dir = _build_multi_rules_dir(tmp_path)
    monkeypatch.setattr(
        rules,
        "settings",
        SimpleNamespace(summaries_dir=str(summaries_dir)),
    )

    callback = _query_callback()
    interaction = _make_interaction()

    asyncio.run(callback(interaction, query="cover save"))

    sent = interaction.followup.sent[0]
    assert sent["wait"] is True
    assert isinstance(sent["view"], rules.RulesResultsView)
    assert len(sent["view"]._select.options) == 2
    assert sent["view"]._select.options[0].default is True


def test_rules_query_single_hit_has_no_view(tmp_path: Path, monkeypatch) -> None:
    summaries_dir = _build_rules_dir(tmp_path)
    monkeypatch.setattr(
        rules,
        "settings",
        SimpleNamespace(summaries_dir=str(summaries_dir)),
    )

    callback = _query_callback()
    interaction = _make_interaction()

    asyncio.run(callback(interaction, query="cover save"))

    assert interaction.followup.sent[0]["view"] is rules.discord.utils.MISSING


def test_rules_results_view_select_edits_to_chosen_hit() -> None:
    async def _run():
        view = rules.RulesResultsView(42, "cover save", _make_hits())
        interaction, edits, _ = _make_component_interaction(user_id=42)
        await view.show(interaction, 1)
        return view, edits

    view, edits = asyncio.run(_run())

    assert len(edits) == 1
    assert "Saving Throws" in edits[0]["content"]
    assert edits[0]["view"] is view
    assert view._select.options[1].default is True


def test_rules_results_view_rejects_non_owner() -> None:
    async def _run():
        view = rules.RulesResultsView(42, "cover save", _make_hits())
        interaction, edits, rejects = _make_component_interaction(user_id=99)
        await view.show(interaction, 1)
        return edits, rejects

    edits, rejects = asyncio.run(_run())

    assert edits == []
    assert len(rejects) == 1
    assert "only the user" in rejects[0][0].lower()
    assert rejects[0][1] is True


def test_rules_query_happy_path_is_ephemeral(tmp_path: Path, monkeypatch) -> None:
    summaries_dir = _build_rules_dir(tmp_path)
    monkeypatch.setattr(
        rules,
        "settings",
        SimpleNamespace(summaries_dir=str(summaries_dir)),
    )

    callback = _query_callback()
    interaction = _make_interaction()

    asyncio.run(callback(interaction, query="cover save"))

    assert interaction.response.deferred == [True]
    assert len(interaction.followup.sent) == 1
    sent = interaction.followup.sent[0]
    assert sent["ephemeral"] is True
    assert sent["allowed_mentions"] is rules.NO_MENTIONS
    assert "Cover and Invulnerable Tests" in sent["content"]


def test_rules_query_service_error_replies_ephemerally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        rules,
        "settings",
        SimpleNamespace(summaries_dir=str(tmp_path / "missing")),
    )

    callback = _query_callback()
    interaction = _make_interaction()

    asyncio.run(callback(interaction, query="cover save"))

    assert len(interaction.followup.sent) == 1
    sent = interaction.followup.sent[0]
    assert sent["ephemeral"] is True
    assert "not available" in str(sent["content"])


def test_rules_query_rate_limit_short_circuits(tmp_path: Path, monkeypatch) -> None:
    summaries_dir = _build_rules_dir(tmp_path)
    monkeypatch.setattr(
        rules,
        "settings",
        SimpleNamespace(summaries_dir=str(summaries_dir)),
    )

    callback = _query_callback()

    async def _run() -> tuple[SimpleNamespace, SimpleNamespace]:
        first = _make_interaction(user_id=7)
        await callback(first, query="cover")
        second = _make_interaction(user_id=7)
        await callback(second, query="cover")
        return first, second

    first, second = asyncio.run(_run())

    assert len(first.followup.sent) == 1
    assert second.followup.sent == []
    assert second.response.deferred == []
    assert len(second.response.sent) == 1
    assert "wait" in second.response.sent[0][0].lower()
    assert second.response.sent[0][1] is True
