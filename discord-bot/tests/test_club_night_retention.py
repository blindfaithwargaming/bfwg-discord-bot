from datetime import UTC, datetime, timedelta

import pytest

from bot.services.club_night_pairing_constraints import PairingConstraintsStore
from bot.services.club_night_signups import SignupStore


@pytest.mark.parametrize("now", [
    datetime(2026, 6, 12, 12, tzinfo=UTC),
    datetime(2026, 9, 30, 12, tzinfo=UTC),
    datetime(2027, 1, 1, 12, tzinfo=UTC),
])
@pytest.mark.parametrize("age_days", [59, 60, 61])
@pytest.mark.parametrize("store_kind", ["signups", "constraints"])
def test_retention_boundary(tmp_path, now, age_days, store_kind) -> None:
    guild_id = "123456789012345678"
    event_date = (now - timedelta(days=age_days)).date()
    created_at = now - timedelta(days=age_days)
    path = tmp_path / "state.json"

    if store_kind == "signups":
        store = SignupStore(path)
        store.add_signup(guild_id, event_date, "11", "Aaron", now=created_at)
        assert store.get_signups(guild_id, event_date)
        store.add_signup(guild_id, now.date(), "22", "Dave", now=now)
        retained = bool(SignupStore(path).get_signups(guild_id, event_date))
    else:
        store = PairingConstraintsStore(path)
        store.add_lock(guild_id, event_date, "11", "22", now=created_at)
        assert store.get_constraints(guild_id, event_date).locked_pairs
        store.set_bye(guild_id, now.date(), "33", now=now)
        retained = bool(
            PairingConstraintsStore(path).get_constraints(guild_id, event_date).locked_pairs
        )

    assert retained is (age_days <= 60)
