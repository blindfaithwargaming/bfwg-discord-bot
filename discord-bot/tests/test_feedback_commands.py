import asyncio
from pathlib import Path
from types import SimpleNamespace

from discord import app_commands

from bot.commands import feedback
from bot.services.feedback import FeedbackStore, OTHER_COMMAND
from bot.services.text_utils import DISCORD_MESSAGE_LIMIT


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
    ) -> object:
        record = {
            "content": content,
            "ephemeral": ephemeral,
            "allowed_mentions": allowed_mentions,
        }
        self.sent.append(record)
        return SimpleNamespace(record=record)


GUILD_ID = 123456789012345678


def _make_interaction(
    user_id: int = 42, guild_id: int | None = GUILD_ID
) -> SimpleNamespace:
    return SimpleNamespace(
        guild_id=guild_id,
        channel_id=2,
        user=SimpleNamespace(id=user_id, display_name="Aaron"),
        response=_Response(),
        followup=_Followup(),
    )


def _submit_callback():
    tree = _CapturingTree()
    feedback.register_feedback_commands(tree)
    group = tree.commands[0]
    return group.get_command("submit").callback


def _choice(value: str = "rules query") -> app_commands.Choice[str]:
    return app_commands.Choice(name=value, value=value)


def _point_settings_at(tmp_path: Path, monkeypatch) -> Path:
    state_path = tmp_path / "state" / "feedback.json"
    monkeypatch.setattr(
        feedback,
        "settings",
        SimpleNamespace(
            feedback_state_path=str(state_path),
            gcs_state_upload_enabled=False,
            gcs_state_bucket=None,
        ),
    )
    return state_path


def test_submit_persists_feedback_and_confirms(tmp_path: Path, monkeypatch) -> None:
    state_path = _point_settings_at(tmp_path, monkeypatch)

    callback = _submit_callback()
    interaction = _make_interaction()

    asyncio.run(
        callback(interaction, command=_choice("rules query"), comment="Excerpt cut off.")
    )

    assert interaction.response.deferred == [True]
    sent = interaction.followup.sent[0]
    assert sent["ephemeral"] is True
    assert sent["allowed_mentions"] is feedback.NO_MENTIONS
    assert "logged" in sent["content"].lower()

    stored = FeedbackStore(state_path).get_feedback(str(GUILD_ID))
    assert len(stored) == 1
    assert stored[0]["command"] == "rules query"
    assert stored[0]["comment"] == "Excerpt cut off."
    assert stored[0]["user_id"] == "42"
    assert stored[0]["display_name"] == "Aaron"


def test_submit_schedules_state_upload(tmp_path: Path, monkeypatch) -> None:
    _point_settings_at(tmp_path, monkeypatch)
    scheduled: list[str] = []
    monkeypatch.setattr(
        feedback.state_sync,
        "schedule_upload",
        lambda path, settings: scheduled.append(path),
    )

    callback = _submit_callback()
    interaction = _make_interaction()

    asyncio.run(
        callback(interaction, command=_choice("rules query"), comment="Great bot.")
    )

    assert scheduled == [feedback.settings.feedback_state_path]


def test_submit_store_failure_does_not_schedule_upload(
    tmp_path: Path, monkeypatch
) -> None:
    state_path = _point_settings_at(tmp_path, monkeypatch)
    state_path.parent.parent.mkdir(parents=True, exist_ok=True)
    # A file where the state directory should be makes mkdir raise OSError.
    state_path.parent.write_text("blocker", encoding="utf-8")
    scheduled: list[str] = []
    monkeypatch.setattr(
        feedback.state_sync,
        "schedule_upload",
        lambda path, settings: scheduled.append(path),
    )

    callback = _submit_callback()
    interaction = _make_interaction()

    asyncio.run(
        callback(interaction, command=_choice("rules query"), comment="Great bot.")
    )

    assert scheduled == []
    assert interaction.followup.sent[0]["content"] == feedback.STORE_ERROR_MESSAGE


def test_submit_other_choice_stored_as_other(tmp_path: Path, monkeypatch) -> None:
    state_path = _point_settings_at(tmp_path, monkeypatch)

    callback = _submit_callback()
    interaction = _make_interaction()

    asyncio.run(
        callback(interaction, command=_choice(OTHER_COMMAND), comment="Love the bot.")
    )

    assert FeedbackStore(state_path).get_feedback(str(GUILD_ID))[0]["command"] == OTHER_COMMAND


def test_submit_rejects_empty_comment(tmp_path: Path, monkeypatch) -> None:
    state_path = _point_settings_at(tmp_path, monkeypatch)

    callback = _submit_callback()
    interaction = _make_interaction()

    asyncio.run(callback(interaction, command=_choice(), comment="   "))

    assert FeedbackStore(state_path).get_feedback(str(GUILD_ID)) == []
    assert "comment" in interaction.followup.sent[0]["content"].lower()


def test_submit_outside_guild_is_rejected(tmp_path: Path, monkeypatch) -> None:
    state_path = _point_settings_at(tmp_path, monkeypatch)

    callback = _submit_callback()
    interaction = _make_interaction(guild_id=None)

    asyncio.run(callback(interaction, command=_choice(), comment="hi"))

    assert FeedbackStore(state_path).get_feedback(str(GUILD_ID)) == []
    assert interaction.followup.sent[0]["content"] == feedback.GUILD_ONLY_MESSAGE


def test_submit_escapes_comment_in_confirmation(tmp_path: Path, monkeypatch) -> None:
    _point_settings_at(tmp_path, monkeypatch)

    callback = _submit_callback()
    interaction = _make_interaction()

    asyncio.run(
        callback(interaction, command=_choice(), comment="ping @everyone **bold**")
    )

    content = interaction.followup.sent[0]["content"]
    assert "@everyone" not in content
    assert "**bold**" not in content


def test_submit_truncates_escaped_confirmation(tmp_path: Path, monkeypatch) -> None:
    _point_settings_at(tmp_path, monkeypatch)

    callback = _submit_callback()
    interaction = _make_interaction()

    asyncio.run(callback(interaction, command=_choice(), comment="*" * 1000))

    content = interaction.followup.sent[0]["content"]
    assert len(content) <= DISCORD_MESSAGE_LIMIT


def test_submit_rate_limit_short_circuits(tmp_path: Path, monkeypatch) -> None:
    _point_settings_at(tmp_path, monkeypatch)

    callback = _submit_callback()

    async def _run() -> tuple[SimpleNamespace, SimpleNamespace]:
        first = _make_interaction(user_id=7)
        await callback(first, command=_choice(), comment="one")
        second = _make_interaction(user_id=7)
        await callback(second, command=_choice(), comment="two")
        return first, second

    first, second = asyncio.run(_run())

    assert len(first.followup.sent) == 1
    assert second.followup.sent == []
    assert second.response.deferred == []
    assert "wait" in second.response.sent[0][0].lower()
