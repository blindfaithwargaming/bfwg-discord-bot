import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from bot.services import state_sync
from bot.services.state_sync import (
    StateSyncError,
    fetch_access_token,
    run_state_sync_tick,
    schedule_upload,
    state_files,
    upload_state_file,
)


def setup_function() -> None:
    state_sync.reset_sync_state()


def _settings(tmp_path: Path, **overrides) -> SimpleNamespace:
    defaults = dict(
        gcs_state_upload_enabled=True,
        gcs_state_bucket="test-bucket",
        club_night_games_state_path=str(tmp_path / "club_night_games.json"),
        feedback_state_path=str(tmp_path / "feedback.json"),
        game_organisation_export_path=str(tmp_path / "game_organisation_export.json"),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _write(path: str, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


def _install_fake_aiohttp(monkeypatch, *, payload=None, error_message: str | None = None):
    calls: list[tuple[str, dict[str, str]]] = []

    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, *, total: int, connect: int) -> None:
            self.total = total
            self.connect = connect

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        def raise_for_status(self) -> None:
            if error_message is not None:
                raise ClientError(error_message)

        async def json(self):
            return payload

    class FakeSession:
        def __init__(self, *, timeout, headers) -> None:
            self.timeout = timeout
            self.headers = headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        def get(self, url: str) -> FakeResponse:
            calls.append((url, self.headers))
            return FakeResponse()

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientError=ClientError,
            ClientSession=FakeSession,
            ClientTimeout=ClientTimeout,
        ),
    )
    return calls


def test_state_files_lists_the_three_mirrored_paths(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert state_files(settings) == (
        settings.club_night_games_state_path,
        settings.feedback_state_path,
        settings.game_organisation_export_path,
    )


def test_upload_state_file_posts_bytes_and_records_mtime(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    _write(settings.club_night_games_state_path, {"games": []})
    calls: list[tuple[str, str, bytes]] = []

    async def fake_token(timeout_seconds: int = 10) -> str:
        return "test-token"

    async def fake_post(url, token, body, timeout_seconds=30) -> None:
        calls.append((url, token, body))

    monkeypatch.setattr(state_sync, "fetch_access_token", fake_token)
    monkeypatch.setattr(state_sync, "_post_object", fake_post)

    assert asyncio.run(
        upload_state_file(settings.club_night_games_state_path, settings)
    )

    (url, token, body) = calls[0]
    assert "/b/test-bucket/o" in url
    assert "name=club_night_games.json" in url
    assert token == "test-token"
    assert json.loads(body) == {"games": []}
    assert settings.club_night_games_state_path in state_sync._last_synced


def test_upload_state_file_missing_file_returns_false_without_token_fetch(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)

    async def fail_token(timeout_seconds: int = 10) -> str:
        raise AssertionError("token fetched for a missing file")

    monkeypatch.setattr(state_sync, "fetch_access_token", fail_token)

    assert not asyncio.run(
        upload_state_file(settings.feedback_state_path, settings)
    )
    assert state_sync._last_synced == {}


def test_fetch_access_token_parses_metadata_response(monkeypatch) -> None:
    calls = _install_fake_aiohttp(
        monkeypatch,
        payload={"access_token": "test-token"},
    )

    token = asyncio.run(fetch_access_token(timeout_seconds=3))

    assert token == "test-token"
    assert calls == [
        (
            state_sync.METADATA_TOKEN_URL,
            {"Metadata-Flavor": "Google"},
        )
    ]


def test_fetch_access_token_failure_raises_state_sync_error(monkeypatch) -> None:
    _install_fake_aiohttp(
        monkeypatch,
        payload={"access_token": "test-token"},
        error_message="metadata denied",
    )

    try:
        asyncio.run(fetch_access_token())
    except StateSyncError as exc:
        assert exc.reason == "token_fetch_failed"
        assert "metadata denied" in str(exc)
    else:
        raise AssertionError("expected StateSyncError")


def test_fetch_access_token_rejects_missing_token(monkeypatch) -> None:
    _install_fake_aiohttp(monkeypatch, payload={})

    try:
        asyncio.run(fetch_access_token())
    except StateSyncError as exc:
        assert exc.reason == "token_fetch_failed"
        assert "access token" in str(exc)
    else:
        raise AssertionError("expected StateSyncError")


def test_upload_state_file_post_failure_does_not_record_mtime(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    _write(settings.club_night_games_state_path, {"games": []})

    async def fake_token(timeout_seconds: int = 10) -> str:
        return "test-token"

    async def fail_post(url, token, body, timeout_seconds=30) -> None:
        raise StateSyncError("boom", reason="upload_failed")

    monkeypatch.setattr(state_sync, "fetch_access_token", fake_token)
    monkeypatch.setattr(state_sync, "_post_object", fail_post)

    def run() -> None:
        try:
            asyncio.run(
                upload_state_file(settings.club_night_games_state_path, settings)
            )
        except StateSyncError as exc:
            assert exc.reason == "upload_failed"
        else:
            raise AssertionError("expected StateSyncError")

    run()
    assert state_sync._last_synced == {}


def test_upload_state_file_captures_mtime_before_read(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    path = settings.club_night_games_state_path
    _write(path, {"games": []})

    async def fake_token(timeout_seconds: int = 10) -> str:
        return "test-token"

    async def racing_post(url, token, body, timeout_seconds=30) -> None:
        # A member reports a game while the upload is in flight.
        _write(path, {"games": ["new"]})
        os.utime(path, (9_999_999_999, 9_999_999_999))

    monkeypatch.setattr(state_sync, "fetch_access_token", fake_token)
    monkeypatch.setattr(state_sync, "_post_object", racing_post)

    asyncio.run(upload_state_file(path, settings))

    # The recorded mtime predates the racing write, so the next tick re-uploads.
    assert state_sync._last_synced[path] < 9_999_999_999


def test_upload_state_file_serializes_uploads_for_the_same_path(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    path = settings.club_night_games_state_path
    _write(path, {"games": ["old"]})

    posted_bodies: list[bytes] = []
    first_call_entered = asyncio.Event()
    release_first_call = asyncio.Event()

    async def fake_token(timeout_seconds: int = 10) -> str:
        return "test-token"

    async def fake_post(url, token, body, timeout_seconds=30) -> None:
        posted_bodies.append(body)
        if len(posted_bodies) == 1:
            first_call_entered.set()
            await release_first_call.wait()

    monkeypatch.setattr(state_sync, "fetch_access_token", fake_token)
    monkeypatch.setattr(state_sync, "_post_object", fake_post)

    async def run() -> None:
        first = asyncio.create_task(upload_state_file(path, settings))
        await first_call_entered.wait()

        # A newer write arrives while the first upload is in flight.
        _write(path, {"games": ["old", "new"]})
        os.utime(path, (os.stat(path).st_mtime + 60,) * 2)
        second = asyncio.create_task(upload_state_file(path, settings))
        await asyncio.sleep(0)  # let `second` reach the lock and block

        release_first_call.set()
        await asyncio.gather(first, second)

    asyncio.run(run())

    assert [json.loads(body) for body in posted_bodies] == [
        {"games": ["old"]},
        {"games": ["old", "new"]},
    ]
    assert state_sync._last_synced[path] == os.stat(path).st_mtime


def test_upload_state_file_does_not_serialize_across_different_paths(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    path_a = settings.club_night_games_state_path
    path_b = settings.feedback_state_path
    _write(path_a, {"games": []})
    _write(path_b, {"feedback": []})

    entered: list[str] = []
    both_entered = asyncio.Event()
    release_event = asyncio.Event()

    async def fake_token(timeout_seconds: int = 10) -> str:
        return "test-token"

    async def fake_post(url, token, body, timeout_seconds=30) -> None:
        entered.append(url)
        if len(entered) == 2:
            both_entered.set()
        await release_event.wait()

    monkeypatch.setattr(state_sync, "fetch_access_token", fake_token)
    monkeypatch.setattr(state_sync, "_post_object", fake_post)

    async def run() -> None:
        task_a = asyncio.create_task(upload_state_file(path_a, settings))
        task_b = asyncio.create_task(upload_state_file(path_b, settings))

        await asyncio.wait_for(both_entered.wait(), timeout=1)
        release_event.set()
        await asyncio.gather(task_a, task_b)

    asyncio.run(run())

    assert len(entered) == 2


def test_fetch_access_token_error_never_logs_a_token(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    settings = _settings(tmp_path)
    _write(settings.feedback_state_path, {"feedback": ["secret-adjacent"]})

    async def fake_token(timeout_seconds: int = 10) -> str:
        return "super-secret-token"

    async def fail_post(url, token, body, timeout_seconds=30) -> None:
        raise StateSyncError("upload rejected", reason="upload_failed")

    monkeypatch.setattr(state_sync, "fetch_access_token", fake_token)
    monkeypatch.setattr(state_sync, "_post_object", fail_post)

    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(run_state_sync_tick(None, settings))

    assert result.status == "failed"
    assert "super-secret-token" not in caplog.text


def test_tick_uploads_all_changed_files(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    for path in state_files(settings):
        _write(path, {"from": Path(path).name})
    uploaded: list[str] = []

    async def fake_upload(path: str, _settings) -> bool:
        uploaded.append(path)
        state_sync._last_synced[path] = os.stat(path).st_mtime
        return True

    monkeypatch.setattr(state_sync, "upload_state_file", fake_upload)

    result = asyncio.run(run_state_sync_tick(None, settings))

    assert result.status == "uploaded"
    assert result.reason == "files=3"
    assert sorted(uploaded) == sorted(state_files(settings))


def test_tick_skips_missing_and_unchanged_files(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    _write(settings.club_night_games_state_path, {"games": []})
    uploaded: list[str] = []

    async def fake_upload(path: str, _settings) -> bool:
        uploaded.append(path)
        state_sync._last_synced[path] = os.stat(path).st_mtime
        return True

    monkeypatch.setattr(state_sync, "upload_state_file", fake_upload)

    first = asyncio.run(run_state_sync_tick(None, settings))
    second = asyncio.run(run_state_sync_tick(None, settings))

    assert first.status == "uploaded"
    assert first.reason == "files=1"
    assert second.status == "skipped"
    assert second.reason == "no_changes"
    assert uploaded == [settings.club_night_games_state_path]


def test_tick_reuploads_a_touched_file(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    path = settings.feedback_state_path
    _write(path, {"feedback": []})
    uploaded: list[str] = []

    async def fake_upload(upload_path: str, _settings) -> bool:
        uploaded.append(upload_path)
        state_sync._last_synced[upload_path] = os.stat(upload_path).st_mtime
        return True

    monkeypatch.setattr(state_sync, "upload_state_file", fake_upload)

    asyncio.run(run_state_sync_tick(None, settings))
    os.utime(path, (os.stat(path).st_mtime + 60,) * 2)
    asyncio.run(run_state_sync_tick(None, settings))

    assert uploaded == [path, path]


def test_tick_one_failure_does_not_block_other_files(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    settings = _settings(tmp_path)
    for path in state_files(settings):
        _write(path, {"from": Path(path).name})
    uploaded: list[str] = []

    async def fake_upload(path: str, _settings) -> bool:
        if path == settings.feedback_state_path:
            raise StateSyncError("denied", reason="upload_failed")
        uploaded.append(path)
        state_sync._last_synced[path] = os.stat(path).st_mtime
        return True

    monkeypatch.setattr(state_sync, "upload_state_file", fake_upload)

    with caplog.at_level(logging.ERROR):
        result = asyncio.run(run_state_sync_tick(None, settings))

    assert result.status == "failed"
    assert result.reason == "upload_failed"
    assert sorted(uploaded) == sorted(
        [settings.club_night_games_state_path, settings.game_organisation_export_path]
    )
    assert settings.feedback_state_path in caplog.text


def test_schedule_upload_is_a_noop_when_disabled(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, gcs_state_upload_enabled=False)

    async def fail_upload(path: str, _settings) -> bool:
        raise AssertionError("upload scheduled while disabled")

    monkeypatch.setattr(state_sync, "upload_state_file", fail_upload)

    async def run() -> None:
        schedule_upload(settings.feedback_state_path, settings)
        assert not state_sync._background_tasks

    asyncio.run(run())


def test_schedule_upload_runs_upload_and_drains_task_set(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    uploaded: list[str] = []

    async def fake_upload(path: str, _settings) -> bool:
        uploaded.append(path)
        return True

    monkeypatch.setattr(state_sync, "upload_state_file", fake_upload)

    async def run() -> None:
        schedule_upload(settings.feedback_state_path, settings)
        assert state_sync._background_tasks
        await asyncio.gather(*state_sync._background_tasks)

    asyncio.run(run())

    assert uploaded == [settings.feedback_state_path]
    assert not state_sync._background_tasks


def test_schedule_upload_logs_failures_instead_of_raising(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    settings = _settings(tmp_path)

    async def fail_upload(path: str, _settings) -> bool:
        raise StateSyncError("denied", reason="upload_failed")

    monkeypatch.setattr(state_sync, "upload_state_file", fail_upload)

    async def run() -> None:
        schedule_upload(settings.feedback_state_path, settings)
        await asyncio.gather(*state_sync._background_tasks)

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())

    assert "State sync upload failed" in caplog.text
