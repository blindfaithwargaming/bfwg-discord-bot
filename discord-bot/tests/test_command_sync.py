from types import SimpleNamespace

from bot.command_sync import format_command_names, registered_command_names


def test_format_command_names_adds_slash_prefixes() -> None:
    assert format_command_names(["admin", "lore", "source"]) == "/admin, /lore, /source"


def test_format_command_names_handles_single_name() -> None:
    assert format_command_names(["lore"]) == "/lore"


def test_format_command_names_handles_empty_list() -> None:
    assert format_command_names([]) == "none"


def test_registered_command_names_returns_sorted_names() -> None:
    tree = SimpleNamespace(
        get_commands=lambda: [
            SimpleNamespace(name="source"),
            SimpleNamespace(name="lore"),
            SimpleNamespace(name="admin"),
        ]
    )
    assert registered_command_names(tree) == ["admin", "lore", "source"]


def test_registered_command_names_handles_empty_tree() -> None:
    tree = SimpleNamespace(get_commands=lambda: [])
    assert registered_command_names(tree) == []
