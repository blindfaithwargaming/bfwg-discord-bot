import os
import re
import tempfile
from dataclasses import dataclass
from datetime import time as dt_time
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


load_dotenv()


DEFAULT_SUMMARIES_DIR_CANDIDATES = (
    Path("../summaries"),
    Path("/app/summaries"),
    Path("summaries"),
)

DEFAULT_CONTENT_DIR_CANDIDATES = (
    Path("content"),
    Path("/app/content"),
)


def require_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


def optional_int_env(name: str) -> int | None:
    value = os.getenv(name)

    if not value:
        return None

    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc


def optional_int_set_env(name: str) -> frozenset[int]:
    value = os.getenv(name)

    if not value:
        return frozenset()

    ids: set[int] = set()

    for part in value.split(","):
        cleaned = part.strip()

        if not cleaned:
            continue

        try:
            ids.add(int(cleaned))
        except ValueError as exc:
            raise RuntimeError(f"{name} must be a comma-separated list of integer IDs.") from exc

    return frozenset(ids)


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None or value == "":
        return default

    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "y", "on"}:
        return True

    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    raise RuntimeError(f"{name} must be a boolean value.")


def default_summaries_dir(candidates: tuple[Path, ...] | None = None) -> str:
    target_candidates = candidates or DEFAULT_SUMMARIES_DIR_CANDIDATES

    for candidate in target_candidates:
        if candidate.exists() and any(candidate.rglob("*.md")):
            return str(candidate)

    for candidate in target_candidates:
        if candidate.exists():
            return str(candidate)

    return str(target_candidates[0])


def _validate_content_file_path(
    value: str,
    env_var: str,
    allowed_roots: tuple[Path, ...] | None = None,
) -> str:
    path = Path(value).expanduser()

    if not path.exists():
        raise RuntimeError(f"{env_var} does not exist: {path}")

    if not path.is_file():
        raise RuntimeError(f"{env_var} must be a file: {path}")

    resolved_path = path.resolve()
    target_allowed_roots = allowed_roots or DEFAULT_CONTENT_DIR_CANDIDATES
    resolved_allowed_roots = tuple(root.expanduser().resolve() for root in target_allowed_roots)

    if not any(resolved_path.is_relative_to(root) for root in resolved_allowed_roots):
        allowed_display = ", ".join(str(root) for root in target_allowed_roots)
        raise RuntimeError(
            f"{env_var} must be inside an expected content root: {allowed_display}. "
            f"Got: {path}"
        )

    return str(resolved_path)


def validate_announcement_template_path(
    value: str,
    allowed_roots: tuple[Path, ...] | None = None,
) -> str:
    return _validate_content_file_path(value, "CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH", allowed_roots)


def validate_reminder_template_path(
    value: str,
    allowed_roots: tuple[Path, ...] | None = None,
) -> str:
    return _validate_content_file_path(value, "CLUB_NIGHT_REMINDER_TEMPLATE_PATH", allowed_roots)


def validate_summaries_dir(
    value: str,
    allowed_roots: tuple[Path, ...] | None = None,
) -> str:
    path = Path(value).expanduser()

    if not path.exists():
        raise RuntimeError(f"SUMMARIES_DIR does not exist: {path}")

    if not path.is_dir():
        raise RuntimeError(f"SUMMARIES_DIR must be a directory: {path}")

    resolved_path = path.resolve()
    target_allowed_roots = allowed_roots or DEFAULT_SUMMARIES_DIR_CANDIDATES
    resolved_allowed_roots = tuple(root.expanduser().resolve() for root in target_allowed_roots)

    if not any(
        resolved_path == root or resolved_path.is_relative_to(root)
        for root in resolved_allowed_roots
    ):
        allowed_display = ", ".join(str(root) for root in target_allowed_roots)
        raise RuntimeError(
            f"SUMMARIES_DIR must be inside an expected summaries root: {allowed_display}. "
            f"Got: {path}"
        )

    return str(resolved_path)


_STADIUM_ALLOWED_HOSTS: frozenset[str] = frozenset({"www.cardiffcitystadiumevents.co.uk"})
_BATTLEFIELDS_ALLOWED_HOSTS: frozenset[str] = frozenset({"www.thebattlefields.co.uk"})

_FETCH_TIMEOUT_MIN = 1
_FETCH_TIMEOUT_MAX = 30
_FETCH_CACHE_MIN = 60
_FETCH_CACHE_MAX = 86_400
_FETCH_MAX_BYTES_MIN = 10_000
_FETCH_MAX_BYTES_MAX = 2_000_000
_REMINDER_DAYS_BEFORE_MIN = 0
_REMINDER_DAYS_BEFORE_MAX = 30
_REMINDER_CHECK_INTERVAL_MIN = 300
_REMINDER_CHECK_INTERVAL_MAX = 86_400
_PAIRING_LOOKBACK_MIN = 0
_PAIRING_LOOKBACK_MAX = 10
_EXPORT_MAX_AGE_HOURS_MIN = 1
_EXPORT_MAX_AGE_HOURS_MAX = 168
_LOCAL_TIME_RE = re.compile(r"^(?P<hour>[01][0-9]|2[0-3]):(?P<minute>[0-5][0-9])$")


def _validate_fetch_url(value: str, env_var: str, allowed_hosts: frozenset[str]) -> str:
    parsed = urlparse(value)

    if parsed.scheme != "https":
        raise RuntimeError(f"{env_var} must use https. Got: {value!r}")

    if parsed.username or parsed.password:
        raise RuntimeError(f"{env_var} must not include credentials.")

    if parsed.hostname not in allowed_hosts:
        allowed_display = ", ".join(sorted(allowed_hosts))
        raise RuntimeError(
            f"{env_var} hostname must be one of: {allowed_display}. Got: {parsed.hostname!r}"
        )

    return value


def _validate_int_bounds(value: int, env_var: str, min_val: int, max_val: int) -> int:
    if not (min_val <= value <= max_val):
        raise RuntimeError(
            f"{env_var} must be between {min_val} and {max_val}. Got: {value}"
        )
    return value


def validate_local_time(value: str | None, env_var: str, default: str) -> dt_time:
    raw = default if value is None else value.strip()
    match = _LOCAL_TIME_RE.fullmatch(raw)

    if not match:
        raise RuntimeError(f"{env_var} must be a 24-hour HH:MM time. Got: {raw!r}")

    return dt_time(int(match.group("hour")), int(match.group("minute")))


def validate_reminder_state_path(value: str, *, scheduler_enabled: bool = False) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise RuntimeError("CLUB_NIGHT_REMINDER_STATE_PATH must not be blank.")

    # Only probe the filesystem when the scheduler will actually write state, so
    # the container default path does not break local runs with the scheduler off.
    if not scheduler_enabled:
        return cleaned

    path = Path(cleaned).expanduser()

    if path.exists() and not path.is_file():
        raise RuntimeError(f"CLUB_NIGHT_REMINDER_STATE_PATH must be a file: {path}")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent):
            pass
    except OSError as exc:
        raise RuntimeError(
            "CLUB_NIGHT_REMINDER_STATE_PATH directory is not writable: "
            f"{path.parent} ({exc})"
        ) from exc

    return cleaned


def validate_signup_state_path(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise RuntimeError("CLUB_NIGHT_SIGNUP_STATE_PATH must not be blank.")

    # No filesystem probe: the sign-up commands are always registered, so a
    # probe of the container default path would break local dev runs. The
    # store creates the directory at write time and commands surface OSError.
    return cleaned


def validate_pairing_history_state_path(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise RuntimeError("CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH must not be blank.")

    # No filesystem probe, for the same reason as the sign-up state path: the
    # store creates the directory at write time and the command surfaces OSError.
    return cleaned


def validate_pairing_constraints_state_path(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise RuntimeError("CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH must not be blank.")

    # No filesystem probe, for the same reason as the sign-up state path: the
    # store creates the directory at write time and the command surfaces OSError.
    return cleaned


def validate_feedback_state_path(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise RuntimeError("FEEDBACK_STATE_PATH must not be blank.")

    # No filesystem probe, for the same reason as the sign-up state path: the
    # store creates the directory at write time and the command surfaces OSError.
    return cleaned


def validate_games_state_path(value: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise RuntimeError("CLUB_NIGHT_GAMES_STATE_PATH must not be blank.")

    # No filesystem probe, for the same reason as the sign-up state path: the
    # store creates the directory at write time and the command surfaces OSError.
    return cleaned


def validate_game_organisation_export_path(
    value: str,
    *,
    export_enabled: bool = False,
) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise RuntimeError("GAME_ORGANISATION_EXPORT_PATH must not be blank.")

    # Only probe the filesystem when the export tick will actually write, so
    # the container default path does not break local runs with the export off.
    if not export_enabled:
        return cleaned

    path = Path(cleaned).expanduser()

    if path.exists() and not path.is_file():
        raise RuntimeError(f"GAME_ORGANISATION_EXPORT_PATH must be a file: {path}")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent):
            pass
    except OSError as exc:
        raise RuntimeError(
            "GAME_ORGANISATION_EXPORT_PATH directory is not writable: "
            f"{path.parent} ({exc})"
        ) from exc

    return cleaned


def validate_gcs_state_bucket(value: str) -> str:
    cleaned = value.strip()

    if cleaned.startswith("gs://"):
        cleaned = cleaned[len("gs://"):]

    if not cleaned:
        raise RuntimeError("GCS_STATE_BUCKET must not be blank.")

    if "/" in cleaned:
        raise RuntimeError(
            f"GCS_STATE_BUCKET must be a bare bucket name, not a path: {cleaned!r}"
        )

    # No network probe: the first state sync tick exercises token, IAM, and
    # bucket for real and logs ERROR on failure.
    return cleaned


def validate_timezone(value: str | None, env_var: str, default: str) -> str:
    raw = default if value is None else value.strip()

    if not raw:
        raise RuntimeError(f"{env_var} must not be blank.")

    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"{env_var} must be a valid IANA timezone. Got: {raw!r}") from exc

    return raw


@dataclass(frozen=True)
class Settings:
    discord_token: str
    discord_guild_id: int | None
    bot_owner_user_ids: frozenset[int]
    sync_commands_on_startup: bool
    summaries_dir: str
    cardiff_stadium_events_url: str
    cardiff_stadium_events_timeout_seconds: int
    cardiff_stadium_events_cache_seconds: int
    cardiff_stadium_events_max_bytes: int
    battlefields_events_url: str
    club_night_forum_channel_id: int
    club_night_forum_tag_id: int | None
    club_night_announcement_template_path: str
    club_night_reminder_template_path: str
    club_night_reminder_scheduler_enabled: bool = False
    club_night_reminder_channel_id: int | None = None
    club_night_reminder_days_before: int = 3
    club_night_reminder_time: dt_time = dt_time(19, 0)
    club_night_reminder_timezone: str = "Europe/London"
    club_night_reminder_check_interval_seconds: int = 3600
    club_night_reminder_state_path: str = "/app/state/scheduled_posts.json"
    club_night_announcement_scheduler_enabled: bool = False
    club_night_announcement_time: dt_time = dt_time(13, 0)
    club_night_signup_state_path: str = "/app/state/club_night_signups.json"
    club_night_pairing_history_state_path: str = (
        "/app/state/club_night_pairing_history.json"
    )
    club_night_pairing_lookback: int = 2
    club_night_pairing_constraints_state_path: str = (
        "/app/state/club_night_pairing_constraints.json"
    )
    feedback_state_path: str = "/app/state/feedback.json"
    club_night_games_state_path: str = "/app/state/club_night_games.json"
    command_docs_channel_id: int | None = None
    game_organisation_channel_id: int | None = None
    game_organisation_export_enabled: bool = False
    game_organisation_export_path: str = "/app/state/game_organisation_export.json"
    game_organisation_export_max_age_hours: int = 24
    gcs_state_upload_enabled: bool = False
    gcs_state_bucket: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        _timeout = optional_int_env("CARDIFF_STADIUM_EVENTS_TIMEOUT_SECONDS")
        _cache = optional_int_env("CARDIFF_STADIUM_EVENTS_CACHE_SECONDS")
        _max_bytes = optional_int_env("CARDIFF_STADIUM_EVENTS_MAX_BYTES")
        _reminder_scheduler_enabled = bool_env(
            "CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED",
            default=False,
        )
        _reminder_channel_id = optional_int_env("CLUB_NIGHT_REMINDER_CHANNEL_ID")
        _reminder_days_before = optional_int_env("CLUB_NIGHT_REMINDER_DAYS_BEFORE")
        _reminder_check_interval = optional_int_env(
            "CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS"
        )
        _pairing_lookback = optional_int_env("CLUB_NIGHT_PAIRING_LOOKBACK")
        _announcement_scheduler_enabled = bool_env(
            "CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED",
            default=False,
        )
        if _reminder_scheduler_enabled and _reminder_channel_id is None:
            raise RuntimeError(
                "CLUB_NIGHT_REMINDER_CHANNEL_ID is required when "
                "CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED=true"
            )
        _game_organisation_export_enabled = bool_env(
            "GAME_ORGANISATION_EXPORT_ENABLED",
            default=False,
        )
        _game_organisation_channel_id = optional_int_env("GAME_ORGANISATION_CHANNEL_ID")
        _game_organisation_export_max_age = optional_int_env(
            "GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS"
        )
        if _game_organisation_export_enabled and _game_organisation_channel_id is None:
            raise RuntimeError(
                "GAME_ORGANISATION_CHANNEL_ID is required when "
                "GAME_ORGANISATION_EXPORT_ENABLED=true"
            )
        _gcs_state_upload_enabled = bool_env("GCS_STATE_UPLOAD_ENABLED", default=False)
        _gcs_state_bucket = os.getenv("GCS_STATE_BUCKET") or None
        if _gcs_state_upload_enabled and _gcs_state_bucket is None:
            raise RuntimeError(
                "GCS_STATE_BUCKET is required when GCS_STATE_UPLOAD_ENABLED=true"
            )

        return cls(
            discord_token=require_env("DISCORD_TOKEN"),
            discord_guild_id=optional_int_env("DISCORD_GUILD_ID"),
            bot_owner_user_ids=optional_int_set_env("BOT_OWNER_USER_IDS"),
            sync_commands_on_startup=bool_env("DISCORD_SYNC_COMMANDS_ON_STARTUP"),
            summaries_dir=validate_summaries_dir(
                os.getenv("SUMMARIES_DIR") or default_summaries_dir()
            ),
            cardiff_stadium_events_url=_validate_fetch_url(
                os.getenv(
                    "CARDIFF_STADIUM_EVENTS_URL",
                    "https://www.cardiffcitystadiumevents.co.uk/",
                ),
                "CARDIFF_STADIUM_EVENTS_URL",
                _STADIUM_ALLOWED_HOSTS,
            ),
            cardiff_stadium_events_timeout_seconds=_validate_int_bounds(
                5 if _timeout is None else _timeout,
                "CARDIFF_STADIUM_EVENTS_TIMEOUT_SECONDS",
                _FETCH_TIMEOUT_MIN,
                _FETCH_TIMEOUT_MAX,
            ),
            cardiff_stadium_events_cache_seconds=_validate_int_bounds(
                900 if _cache is None else _cache,
                "CARDIFF_STADIUM_EVENTS_CACHE_SECONDS",
                _FETCH_CACHE_MIN,
                _FETCH_CACHE_MAX,
            ),
            cardiff_stadium_events_max_bytes=_validate_int_bounds(
                1_000_000 if _max_bytes is None else _max_bytes,
                "CARDIFF_STADIUM_EVENTS_MAX_BYTES",
                _FETCH_MAX_BYTES_MIN,
                _FETCH_MAX_BYTES_MAX,
            ),
            battlefields_events_url=_validate_fetch_url(
                os.getenv(
                    "BATTLEFIELDS_EVENTS_URL",
                    "https://www.thebattlefields.co.uk/events-at-the-battlefields?venue=0&category=22",
                ),
                "BATTLEFIELDS_EVENTS_URL",
                _BATTLEFIELDS_ALLOWED_HOSTS,
            ),
            club_night_forum_channel_id=optional_int_env("CLUB_NIGHT_FORUM_CHANNEL_ID")
            or 1_370_992_608_918_110_258,
            club_night_forum_tag_id=optional_int_env("CLUB_NIGHT_FORUM_TAG_ID"),
            club_night_announcement_template_path=validate_announcement_template_path(
                os.getenv(
                    "CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH",
                    "content/organised-play-announcement.md",
                ),
            ),
            club_night_reminder_template_path=validate_reminder_template_path(
                os.getenv(
                    "CLUB_NIGHT_REMINDER_TEMPLATE_PATH",
                    "content/club_night_reminder.md",
                ),
            ),
            club_night_reminder_scheduler_enabled=_reminder_scheduler_enabled,
            club_night_reminder_channel_id=_reminder_channel_id,
            club_night_reminder_days_before=_validate_int_bounds(
                3 if _reminder_days_before is None else _reminder_days_before,
                "CLUB_NIGHT_REMINDER_DAYS_BEFORE",
                _REMINDER_DAYS_BEFORE_MIN,
                _REMINDER_DAYS_BEFORE_MAX,
            ),
            club_night_reminder_time=validate_local_time(
                os.getenv("CLUB_NIGHT_REMINDER_TIME"),
                "CLUB_NIGHT_REMINDER_TIME",
                "19:00",
            ),
            club_night_reminder_timezone=validate_timezone(
                os.getenv("CLUB_NIGHT_REMINDER_TIMEZONE"),
                "CLUB_NIGHT_REMINDER_TIMEZONE",
                "Europe/London",
            ),
            club_night_reminder_check_interval_seconds=_validate_int_bounds(
                3600
                if _reminder_check_interval is None
                else _reminder_check_interval,
                "CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS",
                _REMINDER_CHECK_INTERVAL_MIN,
                _REMINDER_CHECK_INTERVAL_MAX,
            ),
            club_night_reminder_state_path=validate_reminder_state_path(
                os.getenv("CLUB_NIGHT_REMINDER_STATE_PATH")
                or "/app/state/scheduled_posts.json",
                scheduler_enabled=_reminder_scheduler_enabled
                or _announcement_scheduler_enabled,
            ),
            club_night_announcement_scheduler_enabled=_announcement_scheduler_enabled,
            club_night_announcement_time=validate_local_time(
                os.getenv("CLUB_NIGHT_ANNOUNCEMENT_TIME"),
                "CLUB_NIGHT_ANNOUNCEMENT_TIME",
                "13:00",
            ),
            club_night_signup_state_path=validate_signup_state_path(
                os.getenv("CLUB_NIGHT_SIGNUP_STATE_PATH")
                or "/app/state/club_night_signups.json"
            ),
            club_night_pairing_history_state_path=validate_pairing_history_state_path(
                os.getenv("CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH")
                or "/app/state/club_night_pairing_history.json"
            ),
            club_night_pairing_lookback=_validate_int_bounds(
                2 if _pairing_lookback is None else _pairing_lookback,
                "CLUB_NIGHT_PAIRING_LOOKBACK",
                _PAIRING_LOOKBACK_MIN,
                _PAIRING_LOOKBACK_MAX,
            ),
            club_night_pairing_constraints_state_path=(
                validate_pairing_constraints_state_path(
                    os.getenv("CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH")
                    or "/app/state/club_night_pairing_constraints.json"
                )
            ),
            feedback_state_path=validate_feedback_state_path(
                os.getenv("FEEDBACK_STATE_PATH") or "/app/state/feedback.json"
            ),
            club_night_games_state_path=validate_games_state_path(
                os.getenv("CLUB_NIGHT_GAMES_STATE_PATH")
                or "/app/state/club_night_games.json"
            ),
            command_docs_channel_id=optional_int_env("COMMAND_DOCS_CHANNEL_ID"),
            game_organisation_channel_id=_game_organisation_channel_id,
            game_organisation_export_enabled=_game_organisation_export_enabled,
            game_organisation_export_path=validate_game_organisation_export_path(
                os.getenv("GAME_ORGANISATION_EXPORT_PATH")
                or "/app/state/game_organisation_export.json",
                export_enabled=_game_organisation_export_enabled,
            ),
            game_organisation_export_max_age_hours=_validate_int_bounds(
                24
                if _game_organisation_export_max_age is None
                else _game_organisation_export_max_age,
                "GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS",
                _EXPORT_MAX_AGE_HOURS_MIN,
                _EXPORT_MAX_AGE_HOURS_MAX,
            ),
            gcs_state_upload_enabled=_gcs_state_upload_enabled,
            gcs_state_bucket=(
                validate_gcs_state_bucket(_gcs_state_bucket)
                if _gcs_state_bucket is not None
                else None
            ),
        )


settings = Settings.from_env()
