from discord import app_commands

# No default_permissions: members must be able to use the sign-up commands.
# Organiser-only subcommands (remind, pairings) enforce
# require_publish_permission at runtime instead.
_club_night_group = app_commands.Group(
    name="club-night",
    description="Club night information and tools.",
)
