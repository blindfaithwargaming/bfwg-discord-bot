from bot.services.text_utils import best_excerpt, normalize_markdown, tokens, truncate


def test_normalize_markdown_removes_link_syntax() -> None:
    assert normalize_markdown("[Imperial Fists](https://example.com)") == "Imperial Fists"


def test_normalize_markdown_removes_inline_code_backticks() -> None:
    assert normalize_markdown("`zone mortalis`") == "zone mortalis"


def test_normalize_markdown_removes_heading_markers() -> None:
    assert normalize_markdown("# Sons of Horus\nshock assault legion") == "Sons of Horus shock assault legion"


def test_normalize_markdown_collapses_whitespace() -> None:
    assert normalize_markdown("Iron  Warriors\t prosecute   sieges") == "Iron Warriors prosecute sieges"


def test_tokens_extracts_words_of_at_least_three_chars() -> None:
    result = tokens("Night Lords are it")

    assert "night" in result
    assert "lords" in result
    assert "are" in result
    assert "it" not in result


def test_tokens_lowercases_all_results() -> None:
    result = tokens("Imperial FISTS")

    assert "imperial" in result
    assert "fists" in result
    assert "Imperial" not in result
    assert "FISTS" not in result


def test_truncate_returns_original_when_within_limit() -> None:
    assert truncate("hello", 10) == "hello"


def test_truncate_returns_original_when_equal_to_limit() -> None:
    assert truncate("hello", 5) == "hello"


def test_truncate_adds_ellipsis_when_over_limit() -> None:
    result = truncate("abcdefghij", 7)

    assert result == "abcd..."
    assert len(result) == 7


def test_best_excerpt_returns_sentence_with_most_query_matches() -> None:
    text = (
        "Alpha Legion operatives spread confusion and misdirection.\n"
        "Imperial Fists hold the line with stubborn resolve.\n"
        "The Iron Warriors prosecute siege with attrition."
    )

    excerpt = best_excerpt(text, {"iron", "warriors", "siege"}, 300)

    assert "Iron Warriors" in excerpt


def test_best_excerpt_truncates_long_sentences() -> None:
    text = "The Imperial Fists " + ("hold every siege with disciplined resolve " * 20) + "."

    result = best_excerpt(text, {"imperial"}, 50)

    assert len(result) <= 50
    assert result.endswith("...")


def test_best_excerpt_returns_empty_for_empty_text() -> None:
    assert best_excerpt("", {"imperial"}, 100) == ""
