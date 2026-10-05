"""Mirror agent-facing state files to a private GCS bucket.

Agent commands (/club-night-results, /faction-distribution, /feedback-actions)
read bot state from the bucket via `gcloud storage cat` instead of SSHing into
the production VM. Uploads are best-effort: a failure is logged and never
propagates to the Discord command that triggered it. Sync state is in-memory
only, so every bot restart re-uploads all files — that re-seeds the bucket and
doubles as a live check of token, IAM, and bucket on each boot.

Authentication uses the GCE metadata server, so no service-account keys and no
google-cloud dependency are needed. The access token must never be logged.
"""

import asyncio
import logging
import os
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

from bot.services.club_night_scheduler import SchedulerTickResult

if TYPE_CHECKING:
    from bot.config import Settings

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
GCS_UPLOAD_URL_TEMPLATE = (
    "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
    "?uploadType=media&name={name}"
)

_last_synced: dict[str, float] = {}
_background_tasks: set[asyncio.Task] = set()
_upload_locks: dict[str, asyncio.Lock] = {}


class StateSyncError(RuntimeError):
    """Raised when a state file cannot be uploaded to the bucket.

    reason values: "token_fetch_failed" (metadata server unreachable or
    returned an unusable response), "upload_failed" (GCS HTTP/network failure).
    """

    def __init__(self, message: str, *, reason: str = "unknown") -> None:
        super().__init__(message)
        self.reason = reason


def state_files(settings: "Settings") -> tuple[str, ...]:
    """The state files mirrored to the bucket.

    Single point of control: excluding a file from agent access is a
    one-line change here.
    """
    return (
        settings.club_night_games_state_path,
        settings.feedback_state_path,
        settings.game_organisation_export_path,
    )


def reset_sync_state() -> None:
    _last_synced.clear()
    _background_tasks.clear()
    _upload_locks.clear()


def _upload_lock(path: str) -> asyncio.Lock:
    """One lock per state-file path, so uploads of the same file serialize
    while different files continue to upload concurrently."""
    lock = _upload_locks.get(path)
    if lock is None:
        lock = asyncio.Lock()
        _upload_locks[path] = lock
    return lock


async def fetch_access_token(timeout_seconds: int = 10) -> str:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=timeout_seconds)
    headers = {"Metadata-Flavor": "Google"}

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(METADATA_TOKEN_URL) as response:
                response.raise_for_status()
                payload = await response.json()
    except TimeoutError as exc:
        raise StateSyncError(
            f"The metadata server did not respond within {timeout_seconds}s.",
            reason="token_fetch_failed",
        ) from exc
    except aiohttp.ClientError as exc:
        raise StateSyncError(
            f"Could not fetch an access token from the metadata server: {exc}",
            reason="token_fetch_failed",
        ) from exc

    token = payload.get("access_token") if isinstance(payload, dict) else None

    if not token:
        raise StateSyncError(
            "The metadata server response did not contain an access token.",
            reason="token_fetch_failed",
        )

    return token


async def _post_object(
    url: str,
    token: str,
    body: bytes,
    timeout_seconds: int = 30,
) -> None:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=timeout_seconds)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=body, headers=headers) as response:
                response.raise_for_status()
    except TimeoutError as exc:
        raise StateSyncError(
            f"The GCS upload did not complete within {timeout_seconds}s.",
            reason="upload_failed",
        ) from exc
    except aiohttp.ClientError as exc:
        # aiohttp error strings for these calls carry only URL/status, never
        # the Authorization header, so this message is safe to log.
        raise StateSyncError(
            f"Could not upload to GCS: {exc}",
            reason="upload_failed",
        ) from exc


async def upload_state_file(path: str, settings: "Settings") -> bool:
    """Upload one state file to the bucket. Returns False if the file is absent.

    The mtime is captured before the read: if a write races the upload, the
    recorded mtime predates it and the next tick re-uploads the fresh content.

    Serialized per path so overlapping uploads of the same file cannot finish
    out of order and leave the bucket holding stale JSON: a queued upload
    only reads the file once the preceding upload for that path completes.
    """
    async with _upload_lock(path):
        try:
            mtime = os.stat(path).st_mtime
            body = Path(path).read_bytes()
        except FileNotFoundError:
            return False

        object_name = urllib.parse.quote(Path(path).name, safe="")
        url = GCS_UPLOAD_URL_TEMPLATE.format(
            bucket=settings.gcs_state_bucket, name=object_name
        )

        token = await fetch_access_token()
        await _post_object(url, token, body)

        _last_synced[path] = mtime
        logging.info(
            "State sync uploaded %s to gs://%s/%s (%d bytes).",
            path,
            settings.gcs_state_bucket,
            Path(path).name,
            len(body),
        )
        return True


def schedule_upload(path: str, settings: "Settings") -> None:
    """Fire-and-forget upload after a state write. Never raises."""
    if not settings.gcs_state_upload_enabled:
        return

    async def _upload() -> None:
        try:
            await upload_state_file(path, settings)
        except StateSyncError as exc:
            logging.warning("State sync upload failed for %s: %s", path, exc)
        except Exception:
            logging.exception("State sync upload failed unexpectedly for %s.", path)

    task = asyncio.create_task(_upload(), name="state-sync-upload")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def run_state_sync_tick(bot_client, settings: "Settings") -> SchedulerTickResult:
    """Scheduler backstop: upload any mirrored file changed since its last sync."""
    uploaded = 0
    failed = 0

    for path in state_files(settings):
        try:
            mtime = os.stat(path).st_mtime
        except FileNotFoundError:
            continue

        if mtime <= _last_synced.get(path, float("-inf")):
            continue

        try:
            if await upload_state_file(path, settings):
                uploaded += 1
        except StateSyncError as exc:
            # One failing file must not block the rest.
            failed += 1
            logging.error("State sync upload failed for %s: %s", path, exc)

    if failed:
        return SchedulerTickResult("failed", "upload_failed")

    if uploaded:
        return SchedulerTickResult("uploaded", f"files={uploaded}")

    return SchedulerTickResult("skipped", "no_changes")
