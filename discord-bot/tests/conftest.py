import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Set a dummy token before any bot module is imported during collection,
# so the module-level `settings` object can be constructed without a real token.
os.environ.setdefault("DISCORD_TOKEN", "test-token")

from bot.commands import interaction_helpers  # noqa: E402


@pytest.fixture()
def summaries_dir(tmp_path: Path) -> Path:
    root = tmp_path / "summaries"
    books = root / "Heresy Black Books"
    books.mkdir(parents=True)
    (books / "iron_warriors.md").write_text(
        "# Iron Warriors\n"
        "The Iron Warriors prosecute sieges with methodical attrition and overwhelming firepower.",
        encoding="utf-8",
    )
    (root / "calth.md").write_text(
        "The Calth campaign collapsed from ordered muster into betrayal and desperate survival.",
        encoding="utf-8",
    )
    return root


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    interaction_helpers._command_rate_limiter.reset()


@pytest.fixture()
def club_night_clock(monkeypatch) -> None:
    """Keep command fixtures in June while exercising the real persistent stores."""
    from bot.commands import club_night_pairings
    from bot.services import club_night_pairing_constraints, club_night_signups

    class FixtureDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 6, 12, 12, tzinfo=UTC)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(club_night_pairings, "datetime", FixtureDatetime)
    monkeypatch.setattr(club_night_signups, "datetime", FixtureDatetime)
    monkeypatch.setattr(club_night_pairing_constraints, "datetime", FixtureDatetime)
