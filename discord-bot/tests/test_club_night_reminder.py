from datetime import date

import pytest

from bot.services.club_night_announcement import (
    ReminderTemplateError,
    build_reminder_text,
    load_reminder_body,
)


# --- load_reminder_body ---


def test_load_reminder_body_reads_and_strips_template(tmp_path) -> None:
    template = tmp_path / "reminder.md"
    template.write_text("\nClub night reminder!\n\n", encoding="utf-8")

    assert load_reminder_body(str(template)) == "Club night reminder!"


def test_load_reminder_body_rejects_missing_file(tmp_path) -> None:
    missing = tmp_path / "missing.md"

    with pytest.raises(ReminderTemplateError, match="Could not read reminder template"):
        load_reminder_body(str(missing))


def test_load_reminder_body_rejects_empty_template(tmp_path) -> None:
    template = tmp_path / "reminder.md"
    template.write_text("   \n", encoding="utf-8")

    with pytest.raises(ReminderTemplateError, match="empty"):
        load_reminder_body(str(template))


def test_load_reminder_body_rejects_oversized_template(tmp_path) -> None:
    template = tmp_path / "reminder.md"
    template.write_text("x" * 2001, encoding="utf-8")

    with pytest.raises(ReminderTemplateError, match="Discord messages are limited"):
        load_reminder_body(str(template))


# --- build_reminder_text ---


def test_build_reminder_text_substitutes_all_placeholders() -> None:
    body = "{title} on {date} ({days_label}, {days} days)"
    result = build_reminder_text(body, title="BFWG Night", event_date=date(2026, 5, 22), days_until=3)

    assert "BFWG Night" in result
    assert "Friday 22 May 2026" in result
    assert "in 3 days" in result
    assert "3 days" in result


def test_build_reminder_text_days_label_today() -> None:
    result = build_reminder_text("{days_label}", title="T", event_date=date(2026, 5, 22), days_until=0)

    assert result == "today"


def test_build_reminder_text_days_label_tomorrow() -> None:
    result = build_reminder_text("{days_label}", title="T", event_date=date(2026, 5, 22), days_until=1)

    assert result == "tomorrow"


def test_build_reminder_text_days_label_multiple_days() -> None:
    result = build_reminder_text("{days_label}", title="T", event_date=date(2026, 5, 22), days_until=5)

    assert result == "in 5 days"


def test_build_reminder_text_clamps_negative_days_to_today() -> None:
    result = build_reminder_text("{days} {days_label}", title="T", event_date=date(2026, 5, 22), days_until=-1)

    assert result == "0 today"


def test_build_reminder_text_rejects_unknown_placeholder() -> None:
    with pytest.raises(ReminderTemplateError, match="unknown placeholder"):
        build_reminder_text("{unknown_key}", title="T", event_date=date(2026, 5, 22), days_until=3)
