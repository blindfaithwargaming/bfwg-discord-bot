from discord.utils import escape_markdown, escape_mentions


def escape_public_text(value: str) -> str:
    return escape_mentions(escape_markdown(value))
