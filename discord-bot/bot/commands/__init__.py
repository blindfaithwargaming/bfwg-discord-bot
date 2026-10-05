import discord

import bot.commands.club_night_pairings  # noqa: F401 — registers signup/withdraw/signups/prefer/pairings on _club_night_group
import bot.commands.club_night_admin  # noqa: F401 — registers admin pairing subcommands on _club_night_group
import bot.commands.club_night_reminder  # noqa: F401 — registers remind subcommand on _club_night_group
import bot.commands.club_night_games  # noqa: F401 — registers report-game/remove-game/games on _club_night_group
from bot.commands.admin import register_admin_commands
from bot.commands.club_night_announcement import (
    register_club_night_announcement_command,
    register_club_night_commands,
)
from bot.commands.feedback import register_feedback_commands
from bot.commands.lore import register_lore_commands
from bot.commands.name_generator import register_name_commands
from bot.commands.ping import register_ping_command
from bot.commands.mission import register_mission_commands
from bot.commands.rules import register_rules_commands
from bot.commands.scenario import register_scenario_commands
from bot.commands.source import register_source_command
from bot.commands.stadium_event import register_stadium_event_command


def register_commands(tree: discord.app_commands.CommandTree) -> None:
    register_admin_commands(tree)
    register_ping_command(tree)
    register_stadium_event_command(tree)
    register_club_night_announcement_command(tree)
    register_club_night_commands(tree)
    register_feedback_commands(tree)
    register_lore_commands(tree)
    register_mission_commands(tree)
    register_name_commands(tree)
    register_rules_commands(tree)
    register_scenario_commands(tree)
    register_source_command(tree)
