from bot.services.discord_text import escape_public_text


def test_plain_text_passes_through() -> None:
    assert escape_public_text("Hello world") == "Hello world"


def test_at_everyone_is_escaped() -> None:
    result = escape_public_text("Hello @everyone")
    assert "@everyone" not in result
    assert "@​everyone" in result


def test_at_here_is_escaped() -> None:
    result = escape_public_text("Hello @here")
    assert "@here" not in result
    assert "@​here" in result


def test_numeric_mention_is_escaped() -> None:
    result = escape_public_text("@123456789012345678")
    assert "@123456789012345678" not in result


def test_markdown_characters_are_escaped() -> None:
    result = escape_public_text("**bold** _italic_ `code`")
    assert "**bold**" not in result
    assert r"\*\*bold\*\*" in result


def test_combined_markdown_and_mention() -> None:
    result = escape_public_text("**@everyone**")
    assert "@everyone" not in result
    assert "**@everyone**" not in result