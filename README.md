# BFWG Discord Bot

A Discord bot for [Blind Faith Wargaming](https://github.com/blindfaithwargaming), a
tabletop wargaming club in Cardiff. It runs club nights: event posts and reminders,
member sign-ups, automatic opponent pairings across four game systems, and game-result
reporting. It also has lore, rules and mission-generator commands for Horus Heresy.

**Stack:** Python 3.12, discord.py, aiohttp, Docker, Google Compute Engine (systemd),
Cloud Build, Cloud Logging, Cloud Storage, GitHub Actions.

| | |
|---|---|
| Bot source, commands and services | [`discord-bot/`](discord-bot/) |
| Full feature and operations guide | [`discord-bot/README.md`](discord-bot/README.md) |
| Architecture | [`discord-bot/docs/architecture.md`](discord-bot/docs/architecture.md) |
| Operations runbook | [`discord-bot/docs/operations.md`](discord-bot/docs/operations.md) |
| Metrics service (Go) | [blindfaithwargaming/bfwg-metrics](https://github.com/blindfaithwargaming/bfwg-metrics) |

## Running the tests

```bash
cd discord-bot
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

No Discord token, network or cloud SDK is needed. The tests run against temporary files.

## About this repository

This is a public snapshot of the club's private repository, which remains the deploy
source. It leaves out:

- **The content corpus** (`summaries/`): lore, rules and mission summaries derived from
  Games Workshop, Forge World, Black Library and event-pack publications. Without it the
  corpus-backed commands have no content, and their tests are skipped.
- **The deploy workflow**: production deploys run from the private repository via
  Workload Identity Federation. The docs describe that pipeline as it runs there.
- **Internal planning and agent notes.**
