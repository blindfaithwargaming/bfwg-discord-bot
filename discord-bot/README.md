# BFWG Discord Bot

Python Discord bot for Blind Faith Wargaming.

## Setup

```bash
cd discord-bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Add your bot token to `.env`.

During development, set `DISCORD_GUILD_ID` to your test server ID so slash command changes sync quickly when you run an explicit command sync. Set `DISCORD_SYNC_COMMANDS_ON_STARTUP=true` only when you deliberately want startup to push the local command tree to Discord.

## Configuration

| Variable | Required | Description |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Discord bot token. Keep this out of git. |
| `DISCORD_GUILD_ID` | No | Test or production guild ID for fast guild-scoped command sync. Leave unset for global command sync. |
| `BOT_OWNER_USER_IDS` | No | Comma-separated Discord user IDs allowed to run `/admin` commands even if role permissions change. |
| `DISCORD_SYNC_COMMANDS_ON_STARTUP` | No | Set to `true` only for deliberate bootstrap or local command-sync runs. Defaults to `false`. |
| `SUMMARIES_DIR` | No | Markdown summaries path. Leave blank to auto-detect. If set, it must be `../summaries`, `/app/summaries`, `summaries`, or a child path under one of those expected roots. |
| `CLUB_NIGHT_FORUM_CHANNEL_ID` | No | Discord forum channel used for organised play announcements and reminder date lookup. Defaults to the production `game-organisation` forum. |
| `CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED` | No | Enables the automatic in-bot club-night reminder scheduler. Defaults to `false` in local config; production deploy config enables it. |
| `CLUB_NIGHT_REMINDER_CHANNEL_ID` | Required when scheduler enabled | Discord text channel where automatic reminders are posted. |
| `CLUB_NIGHT_REMINDER_DAYS_BEFORE` | No | Number of days before the forum-posted club-night date to send the reminder. Defaults to `3`; `0` allows same-day reminders. |
| `CLUB_NIGHT_REMINDER_TIME` | No | Local time for the first eligible reminder tick. Defaults to `19:00`. |
| `CLUB_NIGHT_REMINDER_TIMEZONE` | No | IANA timezone for reminder and announcement timing. Defaults to `Europe/London`. |
| `CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED` | No | Enables the automatic club-night announcement scheduler, which creates the next organised play forum post the day after the previous club night. Defaults to `false` in local config; production deploy config enables it. |
| `CLUB_NIGHT_ANNOUNCEMENT_TIME` | No | Local time from which the automatic announcement post is eligible. Defaults to `13:00`. |
| `CLUB_NIGHT_SIGNUP_STATE_PATH` | No | JSON file storing club-night sign-ups and opponent preferences. Defaults to `/app/state/club_night_signups.json` (the VM state mount); the directory is created at first write. |
| `CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH` | No | JSON file storing posted pairings per event thread, grouped by game, used for repeat-opponent avoidance. Defaults to `/app/state/club_night_pairing_history.json` (the VM state mount); the directory is created at first write. |
| `CLUB_NIGHT_PAIRING_LOOKBACK` | No | How many previous recorded events of the same game `/club-night pairings` checks when avoiding repeat opponents (0–10, default `2`). `0` disables avoidance without disabling recording. |
| `CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH` | No | JSON file storing organiser pairing constraints (locks, avoids, forced bye) set by the admin pairing commands. Defaults to `/app/state/club_night_pairing_constraints.json` (the VM state mount); the directory is created at first write. |
| `FEEDBACK_STATE_PATH` | No | JSON file storing `/feedback submit` bug reports and suggestions per guild. Defaults to `/app/state/feedback.json` (the VM state mount); the directory is created at first write. |
| `CLUB_NIGHT_GAMES_STATE_PATH` | No | JSON file storing `/club-night report-game` results. Never pruned — it is the game-results history the `/club-night-results` agent command analyses. Defaults to `/app/state/club_night_games.json` (the VM state mount); the directory is created at first write. |
| `COMMAND_DOCS_CHANNEL_ID` | No | Discord text channel where a community command list is auto-maintained. When set, `/admin sync-commands` rewrites that channel's post listing member-facing commands and their parameters. Use a channel dedicated to this post — the bot deletes its own previous messages there before reposting. Leave unset to disable. |
| `GAME_ORGANISATION_EXPORT_ENABLED` | No | Enables the daily game organisation export scheduler tick. Defaults to `false` in local config; production deploy config enables it. |
| `GAME_ORGANISATION_CHANNEL_ID` | Required when export enabled | Discord forum channel snapshotted by the export (production: the `game-organisation` forum, `1370992608918110258`). |
| `GAME_ORGANISATION_EXPORT_PATH` | No | JSON file the export writes the raw forum snapshot to. Defaults to `/app/state/game_organisation_export.json` (the VM state mount). |
| `GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS` | No | How old the snapshot must be before an hourly tick re-exports (1–168, default `24`). |

## Game organisation export

When `GAME_ORGANISATION_EXPORT_ENABLED` is `true`, the scheduler loop snapshots the
`GAME_ORGANISATION_CHANNEL_ID` forum — every active and archived thread with its
messages (author, display name, bot flag, timestamp, content), the full server history —
to `GAME_ORGANISATION_EXPORT_PATH` whenever the existing snapshot is older than
`GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS`. The export is raw data only; no faction or
game interpretation happens in the bot. The Claude `/faction-distribution` agent command
(and its Codex twin) reads the file over SSH and works out the distribution of factions
members have played, marking players with no stated faction as **Unknown**.

The bot requests the privileged **Message Content** gateway intent so the export can
read member messages. The matching **Message Content Intent** toggle must be enabled on
the bot's application page in the Discord Developer Portal (Bot → Privileged Gateway
Intents) *before* deploying — a requested-but-unapproved privileged intent makes the
gateway refuse the connection, and without the intent the export would contain empty
message content and `/faction-distribution` would report everyone as Unknown.

## Community command list

When `COMMAND_DOCS_CHANNEL_ID` is set, running `/admin sync-commands` (re)generates a
post in that channel documenting every member-facing slash command, with parameters.
Admin-only and organiser-only commands are intentionally omitted — staff use the in-repo
documentation. The post is rebuilt only on `/admin sync-commands`, which is already the
documented post-deploy step, so it stays in step with the live command tree. The bot
removes its own previous messages in that channel before posting, so the channel should be
dedicated to this list.

## Development Workflow

Use a `development` branch for bot changes:

```bash
git switch -c development
```

Install development dependencies:

```bash
cd discord-bot
python -m pip install -r requirements-dev.txt
```

Run the local test suite:

```bash
python -m pytest
```

The GitHub Actions workflow in `.github/workflows/discord-bot-tests.yml` runs the same tests on pushes to `development`. When the tests pass, a promotion job fast-forwards `main` to the tested `development` commit. `.github/workflows/deploy.yml` then runs from `workflow_run` and submits Cloud Build for that tested commit.

The promotion job expects `development` to include the latest `main`. If `main` has moved independently, the workflow will fail instead of creating an untested merge commit. Bring `development` up to date with `main`, then push again.

## Run

```bash
python -m bot
```

The commands are `/admin sync-commands`, `/admin diagnostics`, `/admin config-check`, `/ping`, `/stadium-event`, `/post-club-night`, `/club-night next`, `/club-night preview`, `/club-night remind`, `/club-night signup-info`, `/club-night signup`, `/club-night my-signup`, `/club-night withdraw`, `/club-night signups`, `/club-night prefer`, `/club-night report-game`, `/club-night remove-game`, `/club-night games`, `/club-night pairings`, `/club-night add-player`, `/club-night remove-player`, `/club-night set-side`, `/club-night set-format`, `/club-night lock-pair`, `/club-night avoid-pair`, `/club-night set-bye`, `/club-night clear-signups`, `/club-night preview-pairings`, `/lore random`, `/lore search`, `/source`, `/rules query`, `/mission random`, `/name generator`, `/scenario seed`, `/scenario twist`, and `/feedback submit`.

## Command Sync

The bot registers slash commands in memory on startup, but it does not sync them to Discord by default. This avoids an old production revision reconnecting during deployment and overwriting Discord with an outdated command tree.

Use one of these explicit sync paths after adding, removing, or changing slash commands:

```bash
python -m bot sync-commands
```

or run `/admin sync-commands` in Discord after that admin command has already been synced once.

Set `DISCORD_SYNC_COMMANDS_ON_STARTUP=true` only for a deliberate bootstrap or local development run. In production, keep it `false` after commands are synced.

## Commands

Public bot responses disable Discord mention parsing and escape user-supplied display text before formatting, so command inputs cannot ping roles/users or inject unexpected Markdown.

### `/admin sync-commands`

Syncs the current slash command tree to Discord. Use this after deploying code that changes commands. If `COMMAND_DOCS_CHANNEL_ID` is set, it also rewrites the community command list post in that channel (see [Community command list](#community-command-list)).

| Option | Required | Description |
| --- | --- | --- |
| None | No | This command takes no options. |

Example:

```text
/admin sync-commands
```

Response visibility: ephemeral. Only the admin who ran the command sees the synced command count and command names.

Permissions:

- The command is restricted to Discord users with `Administrator`.
- The bot also performs a runtime administrator check, or allows users listed in `BOT_OWNER_USER_IDS`.
- If `DISCORD_GUILD_ID` is set, it syncs guild commands for that server.
- If `DISCORD_GUILD_ID` is unset, it syncs global commands.

### `/admin diagnostics`

Returns a health summary for the running bot instance without requiring log access.

| Option | Required | Description |
| --- | --- | --- |
| None | No | This command takes no options. |

Example:

```text
/admin diagnostics
```

Response visibility: ephemeral. Only the admin who ran the command sees the report.

The response includes:

- Bot user and Discord ID.
- Summaries directory path and `.md` file count.
- Registered command names and count.
- Configured forum channel ID.
- Number of bot owner IDs configured (count only — the IDs themselves are not shown).

Permissions:

- The command is restricted to Discord users with `Administrator`.
- The bot also performs a runtime administrator check, or allows users listed in `BOT_OWNER_USER_IDS`.

### `/admin config-check`

Checks the bot's runtime configuration and reports a PASS or FAIL for each critical dependency without requiring log access.

| Option | Required | Description |
| --- | --- | --- |
| None | No | This command takes no options. |

Example:

```text
/admin config-check
```

Response visibility: ephemeral. Only the admin who ran the command sees the results.

The response shows a PASS or FAIL for each of these checks:

- **Summaries dir** — the configured `SUMMARIES_DIR` exists and contains at least one `.md` file.
- **Announcement template** — the template file exists, is a regular file, and is non-empty.
- **Forum channel** — the configured `CLUB_NIGHT_FORUM_CHANNEL_ID` resolves to a Discord forum channel the bot can access.
- **Forum tag** — if `CLUB_NIGHT_FORUM_TAG_ID` is set, confirms the tag ID is valid for that forum channel. Shows `not configured` if the env var is unset.

Permissions:

- The command is restricted to Discord users with `Administrator`.
- The bot also performs a runtime administrator check, or allows users listed in `BOT_OWNER_USER_IDS`.

### `/ping`

Checks whether the bot is online and able to respond to slash commands.

| Option | Required | Description |
| --- | --- | --- |
| None | No | This command takes no options. |

Example:

```text
/ping
```

Response visibility: ephemeral. Only the user who ran the command sees `Pong.`.

Permissions:

Unrestricted — any server member can use this command.

### `/stadium-event`

Checks the Cardiff City Stadium events page for listed events on a date. This is useful before planning or posting a club night.

| Option | Required | Description |
| --- | --- | --- |
| `date` | No | Date to check. Supported examples include `2026-07-04`, `04/07/2026`, and `4 July 2026`. If omitted, the bot uses the next Blind Faith club night from The Battlefields event page. |
| `refresh` | No | Set to `true` to fetch fresh event data instead of using the cache. Defaults to `false`. |

Examples:

```text
/stadium-event
/stadium-event date:2026-07-04
/stadium-event date:4 July 2026 refresh:true
```

Response visibility: ephemeral.

Notes:

- The command rejects past dates because the stadium listings may no longer be reliable.
- If cached event data is used, the response includes the cache fetch time.
- The lookup uses `CARDIFF_STADIUM_EVENTS_URL`, `BATTLEFIELDS_EVENTS_URL`, `CARDIFF_STADIUM_EVENTS_TIMEOUT_SECONDS`, `CARDIFF_STADIUM_EVENTS_CACHE_SECONDS`, and `CARDIFF_STADIUM_EVENTS_MAX_BYTES`.
- The cache keeps the command responsive if the ticketing site is slow or briefly blocks server-side requests.

Permissions:

Unrestricted — any server member can use this command.

### `/post-club-night`

Creates an organised play announcement post in the configured Discord forum channel for the next Blind Faith club night.

| Option | Required | Description |
| --- | --- | --- |
| `refresh` | No | Set to `true` to fetch fresh club-night and stadium event data instead of using the cache. Defaults to `false`. |

Examples:

```text
/post-club-night
/post-club-night refresh:true
```

Response visibility: ephemeral confirmation to the user who ran the command. The created forum post is public in the configured forum channel.

Permissions:

- The command requires the user to have Discord's `Manage Threads` permission.
- The bot needs permission to create forum posts in `CLUB_NIGHT_FORUM_CHANNEL_ID`.
- If the forum requires tags, set `CLUB_NIGHT_FORUM_TAG_ID` to a valid tag ID for that forum.

Notes:

- The post body comes from `content/organised-play-announcement.md` by default.
- The command appends a Cardiff City Stadium status check to the announcement.
- The command checks active and recently archived forum thread titles before posting, and skips creation if a matching announcement already exists.
- This command uses the Battlefields ticketing page as the source of truth for the next club-night date.
- The command uses `CLUB_NIGHT_FORUM_CHANNEL_ID`, `CLUB_NIGHT_FORUM_TAG_ID`, and `CLUB_NIGHT_ANNOUNCEMENT_TEMPLATE_PATH`.
- If another `/post-club-night` request is still running in the same bot process, the older request is cancelled before it can post.
- When `CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED` is `true`, the bot also creates this forum post automatically from `CLUB_NIGHT_ANNOUNCEMENT_TIME` (default `13:00` local) on the day after the previous club night, once the Battlefields page lists the next event. The scheduler skips while a current or future announcement post exists, so a manual `/post-club-night` simply pre-empts it.

### Organised play event threads

Every `/club-night` command must be called inside its event thread in
`CLUB_NIGHT_FORUM_CHANNEL_ID`. Titles use `<Game> Organised Play - <day><ordinal> <month>`,
for example `Bushido Organised Play - 6th October` or `Heresy Organised Play - 8th October`.
Legacy `Organised Play - <date>` titles mean Heresy. Game names are case-insensitive;
`Horus Heresy` also means Heresy. The represented names for 3rd Edition and The Old World
(and their short names) are normalised to the same game choices. Other prefixes retain
generic singles support when used as the thread default.
Commands reject other channels and unrecognised titles instead of selecting another event.

Organisers create new non-Heresy event threads manually, then call `/club-night signup-info`.
There are no automatic announcements or reminders for those games. Existing Heresy
scheduling remains enabled according to its settings and only considers Heresy posts.
`/post-club-night` now creates titles beginning `Heresy Organised Play`; legacy titles
still prevent duplicate announcements.

### `/club-night signup-info`

Admin/owner only. Posts the signup procedure publicly in the current event thread,
including optional game-specific fields, withdrawal, pairings and result commands.
No parameters. Members still follow ticket/payment instructions in the event's opening
post; the bot does not purchase or verify tickets.

### `/club-night next`

Admin/owner only. Shows the **current thread's event** and its date. Heresy events also
include a Cardiff City Stadium conflict check. Replies ephemerally. The existing name
is retained; this command no longer looks up another event.

| Option | Required | Description |
| --- | --- | --- |
| `refresh` | No | Refresh the Heresy stadium check. Defaults to `false`; unused for other games. |

### `/club-night preview`

Admin/owner only. Previews the current event's signup procedure ephemerally. Use
`/club-night signup-info` to publish it. The existing optional `refresh` parameter is
retained for compatibility and has no effect; this preview uses the current thread.

### `/club-night remind`

Posts the existing Heresy reminder text in the current Heresy event thread, using that thread’s date. Other games direct organisers to `/club-night signup-info`.

| Option | Required | Description |
| --- | --- | --- |
| `days_before` | No | Only post if the forum-posted club night is within this many days. Defaults to `3`; use `0` for same-day reminders. |

Example:

```text
/club-night remind
```

Response visibility: ephemeral confirmation to the user who ran the command. The reminder itself is public in the current channel.

Permissions:

- The command requires the user to have Discord's `Manage Threads` permission.
- The bot needs permission to read active and recent archived threads in `CLUB_NIGHT_FORUM_CHANNEL_ID`.
- The bot needs permission to send messages in the current channel.

Notes:

- Reminder dates come from Discord forum posts titled like `Organised Play - 11th June`.
- The manual reminder and automatic scheduler share `content/club_night_reminder.md`.
- `/post-club-night` still uses the Battlefields ticketing page to create the main announcement.

### `/club-night signup`

Signs the caller up for the current event. **No options are required.** With no `game`
selection, the game always comes from the thread title—even on a repeat signup.
Select another game to play it at the same club night. There is one signup per player
per date: signing up in another thread for that date moves their signup there.
Bushido and other non-Heresy selections use singles pairings without allegiance sides.

The current choices come from the Game Rep role description in committee PR #27:
Horus Heresy, Warhammer 40,000 3rd Edition, Bushido, and Warhammer: The Old World.
The bot keeps this list in `bot/services/organised_play.py`; live Discord-role discovery
is deferred. Update the registry if the represented games change.

| Option | Required | Description |
| --- | --- | --- |
| `game` | No | One of the four represented games. Defaults to the thread title; replaces your signup for this date. |
| `side` | No | Heresy only: `Loyalist`, `Traitor`, or `Either`. Defaults to `Either`. Re-running the command updates your side without losing a stored opponent preference. |
| `format` | No | Heresy only: `Solo` (no teammate, but may face a team in a 2v1), `Doubles` (team up eagerly), or `Either` (team up if it helps). The first sign-up defaults to `Solo`; omitting it on a later sign-up preserves the stored choice. |
| `faction` | No | Optional faction. Bushido uses the official GCT faction suggestions; Heresy uses the existing lore index; other games accept free text. |
| `play_style` | No | `Learning`, `Casual` or `Competitive`. Informational preference for organisers and opponents. |
| `rice` | No | Bushido only: preferred level, from 1–1000 rice. The event’s usual level is 100 rice. |

Example:

```text
/club-night signup
/club-night signup side:Loyalist format:Doubles
/club-night signup game:Bushido faction:Ito Clan play_style:Learning rice:50
```

Response visibility: ephemeral.

Permissions:

- Anyone in the server can sign up.
- Run the command inside the event thread in `CLUB_NIGHT_FORUM_CHANNEL_ID`.

Notes:

- Sign-ups and organiser constraints are isolated by event thread, including multiple games or threads on the same date.
- Omitted optional details preserve previous values when the selected game stays the same. Changing games clears faction, play style, rice, allegiance, doubles format and opponent preference before applying the new choices. Withdraw and sign up again to clear details without switching games.
- Optional details appear in signups and pairings; they do not restrict matching. Bushido faction suggestions come from [GCT Studios](https://gctstudios.com/collections), checked 2026-10-05.
- Doubles is opt-in. `Solo` players are never placed on a multi-player side.
- Entries older than 60 days are pruned automatically. Signup/pairing tools reject events outside that window; game results remain available.

### `/club-night my-signup`

Privately confirms your signup in the current event thread and shows your saved game,
faction, play style, opponent preference, and game-specific options (Heresy side/format
or Bushido rice). Unselected optional fields are shown explicitly. Takes no parameters;
any member can use it. Every response is ephemeral and the command changes no signup data.
If you signed up in a different thread for the same date, run the command there.

### `/club-night withdraw`

Removes the caller's sign-up (and their stored opponent preference) for the current event.

Example:

```text
/club-night withdraw
```

Response visibility: ephemeral.

### `/club-night signups`

Lists who is signed up for the current event, including the optional faction, play style and rice details (plus sides for Heresy).

Example:

```text
/club-night signups
```

Response visibility: ephemeral.

Permissions:

- The command is restricted to Discord users with `Administrator`.
- The bot also performs a runtime administrator check, or allows users listed in `BOT_OWNER_USER_IDS`.

### `/club-night prefer`

Records the caller's preferred opponent for the current event. Preferences are one-way until reciprocated — pairings only honour a preference when both players prefer each other.

| Option | Required | Description |
| --- | --- | --- |
| `player` | No | The member you would like to play. Required unless `clear:true` is set. |
| `clear` | No | Set to `true` to clear your stored preference instead. Defaults to `false`. |

Example:

```text
/club-night prefer player:@Dave
/club-night prefer clear:true
```

Response visibility: ephemeral.

Permissions:

- The caller must already be signed up via `/club-night signup`.
- You cannot prefer yourself or a bot.

### `/club-night report-game`

Records a played game from inside a club-night thread. Supports two sides of up to four players each (1v1 through 4v4, lopsided sides allowed). The result is stored as structured JSON on the VM (`CLUB_NIGHT_GAMES_STATE_PATH`, default `/app/state/club_night_games.json`) — the file is never pruned and is the corpus the Claude `/club-night-results` agent command analyses for win rates and faction performance.

| Option | Required | Description |
| --- | --- | --- |
| `result` | Yes | `Side 1 won`, `Side 2 won`, or `Draw`. |
| `game` | No | Game actually played; defaults to the thread title. Choose it explicitly for another game at the same club night. |
| `side1_player1` / `side2_player1` | Yes | The first player on each side. |
| `side1_faction1` / `side2_faction1` | No | Optional faction; suggestions match the selected game (or thread default). Free text allowed. Omitted factions are recorded as `Not specified`. |
| `side1_player2-4` / `side1_faction2-4` | No | Additional side 1 players for team games; factions are optional. A faction alone still needs a player. |
| `side2_player2-4` / `side2_faction2-4` | No | Additional side 2 players for team games. |
| `points_size` | No | Points the game was played at (e.g. `3000`); use `rice` for Bushido. |
| `rice` | No | Bushido only: actual rice level (1–1000). |
| `mission` | No | The mission played (autocompletes from the missions corpus; free text allowed). |
| `notes` | No | A short note about the game (sanitised, max 1000 characters). |
| `campaign_hex` | No | The campaign map hex the game was fought over (1–9999). |

Example:

```text
/club-night report-game result:Side 1 won side1_player1:@Aaron side1_faction1:Sons of Horus side2_player1:@Dave side2_faction1:Imperial Fists points_size:3000 mission:Onslaught
```

Response visibility: a public game summary is posted to the club-night thread (with a short game ref for removals); the caller also gets an ephemeral confirmation with the full game id.

Permissions:

- Must be run inside a club-night thread in the organised play forum (a thread titled `<Game> Organised Play - <date>`, or the legacy Heresy format). Past club nights are accepted — results are often reported after the night.
- Bots cannot be listed as players, and each player can only appear once per game.

### `/club-night remove-game`

Removes a recorded game by its game ref (from the public summary) or full game id. There is no in-place edit — remove the game and report it again.

| Option | Required | Description |
| --- | --- | --- |
| `game_id` | Yes | The game ref from the confirmation post (at least 8 characters) or the full game id. |

Example:

```text
/club-night remove-game game_id:9f3c2a7e
```

Response visibility: ephemeral.

Permissions:

- Only the member who reported the game can remove it; Discord administrators and bot owners can remove any game in the current event thread.

### `/club-night games`

Lists the games recorded in the current club-night thread, with their game refs — handy before a `/club-night remove-game`.

Example:

```text
/club-night games
```

Response visibility: ephemeral.

Permissions:

- Must be run inside a club-night thread in the organised play forum.

### `/club-night pairings`

Generates pairings for the current event and posts them publicly in its thread. Players are paired only with others selecting the same game, even when a lock or mutual preference crosses games. Rematch avoidance uses history for the selected game, regardless of the host thread title. Bushido and other non-Heresy games use singles; optional signup details do not override rematch avoidance.

Example:

```text
/club-night pairings
```

Response visibility: ephemeral confirmation to the user who ran the command. The pairings post itself is public in the current channel.

Permissions:

- The command requires bot owner, Discord administrator, or `Manage Threads` permission (same rule as `/club-night remind`).
- The bot needs permission to send messages in the current channel.

Notes:

- Pairing priority: forced byes, organiser locks, and mutual opponent preferences resolve as fixed 1v1s first. Remaining players who opted into doubles are formed into faction-compatible teams, with explicit `Doubles` choices ahead of flexible `Either` choices and bye avoidance ahead of keeping a team together. Loyalist units are then matched against Traitor units where possible, repeat opponents are avoided best-effort, and leftovers are matched randomly. Any unmatched unit is reported as bye(s) for the organiser to resolve.
- Repeat-opponent avoidance: the command avoids re-pairing players who met within the last `CLUB_NIGHT_PAIRING_LOOKBACK` recorded club nights (default 2). Two overrides take precedence — a mutual preference pair is always honoured, and Loyalist↔Traitor side matching is never broken to dodge a rematch. When a rematch is unavoidable it is posted with a `*(rematch)*` marker (a pair can show both `*(requested)*` and `*(rematch)*`).
- Re-running is safe: each posted pairing overwrites the recorded pairing for that event thread (last-write-wins), so the most recent post is what counts as played. Deleting the history file resets avoidance from scratch.
- Organiser constraints set via the admin commands below (locks, avoids, forced bye) are applied here too: locked pairs are formed first (ignoring sides), forced byes are set aside up front, and avoided pairs are honoured best-effort. Markers `*(locked)*` and `*(avoid override)*` join the existing `*(requested)*`/`*(rematch)*` set.

### Admin pairing tools

These nine `/club-night` subcommands are admin-only at runtime (bot owner or Discord administrator) and are hidden from the community command list. They give an organiser manual control over a club night's pairings. All reply ephemerally and operate on the current event thread.

| Command | Options | Behaviour |
| --- | --- | --- |
| `/club-night add-player` | `member`, `game?`, `side?`, `format?`, `faction?`, `play_style?`, `rice?` | Sign a member up (walk-ins / paper sign-ups); omitting `format` preserves their existing choice or defaults a new member to `Solo`. |
| `/club-night remove-player` | `member` | Remove a member's sign-up. |
| `/club-night set-side` | `member`, `side` | For a member signed up for Heresy: override that member's side. |
| `/club-night set-format` | `member`, `format` | For a member signed up for Heresy: override that member's doubles format without changing their sign-up time or other fields. |
| `/club-night lock-pair` | `player1`, `player2`, `clear?` | Force two players together (wins over sides and preferences). `clear:true` removes the lock. |
| `/club-night avoid-pair` | `player1`, `player2`, `clear?` | Never pair these two if it can be helped (best-effort; falls back and marks `*(avoid override)*` when unavoidable). `clear:true` removes it. |
| `/club-night set-bye` | `member?`, `clear?` | Force who sits out. `clear:true` removes the forced bye. |
| `/club-night clear-signups` | — | Wipe all sign-ups **and** pairing constraints for the night, behind a confirmation button. |
| `/club-night preview-pairings` | — | Privately preview the pairings with **Reroll** (reshuffle) and **Post** (publish) buttons. If signups or constraints change, generate a new preview before posting. |

Constraints are stored per guild and event thread in `CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH` (default `/app/state/club_night_pairing_constraints.json`, pruned after 60 days). They apply to both `/club-night pairings` and `/club-night preview-pairings`. New commands require one `/admin sync-commands` after the deploy that ships them.

Event state remains in the existing JSON files. Scoped keys are
`<ISO date>|<URL-encoded game>|<thread ID>` under the guild. Signup/constraint keys
use the thread’s game; each signup also stores its selected game. History keys use
the game actually paired, so one thread can have several history records. Reposting
replaces all game histories for that thread/date without overwriting another thread. Date-only legacy signup
and constraint records are visible only to Heresy, and move to that thread's key on
the first write. Legacy pairing history remains Heresy history; retention keeps the
latest 12 events **per game**. Game results retain their thread IDs and add `game` and
optional `rice`; old records without `game` mean Heresy. Game deletion is limited to
the current thread, even for administrators.

After deployment, run `/admin sync-commands`, then call `/club-night signup-info` in
an event thread. Back up the signup, constraint and pairing-history files before a
rollback to older bot code: older versions do not understand scoped keys and may
prune them. Keep the pre-upgrade files if reverting to an older image.


### `/scenario seed`

Creates a public Horus Heresy mission seed from the Markdown summaries. The command scans the configured summaries directory for source cues matching the requested attacker and defender, then formats a Discord-safe mission prompt.

| Option | Required | Description |
| --- | --- | --- |
| `attacker` | Yes | Attacking faction, legion, warband, or force. |
| `defender` | Yes | Defending faction, legion, warband, or force. |
| `tone` | No | Optional tone. Supported examples include `grim`, `desperate`, `heroic`, `horror`, `siege`, and `traitor`. Defaults to `grim`. |

Examples:

```text
/scenario seed attacker:Sons of Horus defender:Imperial Fists
/scenario seed attacker:World Eaters defender:Ultramarines tone:desperate
/scenario seed attacker:Night Lords defender:Dark Angels tone:horror
```

Response visibility: public, so the generated mission seed appears in the channel.

Notes:

- The command does not call an external LLM or API.
- The summaries directory is configured with `SUMMARIES_DIR`; it is validated against the expected local and container summaries roots at startup.
- The response includes a title, attacker, defender, tone, briefing, objectives, complication, and source cues.
- Responses are trimmed to Discord's 2000-character message limit.

Permissions:

Unrestricted — any server member can use this command.

### `/mission random`

Randomly generates a playable Horus Heresy 3rd edition mission, including a primary objective, deployment zone, and secondary objectives with VP values. Optionally includes an Engine Kill (Titan) mission in the pool and adds a narrative twist.

| Option | Required | Description |
| --- | --- | --- |
| `deployment` | No | Fix the deployment zone by slug (e.g. `dawn-of-war`). Chosen randomly if omitted. |
| `titans` | No | Set to `true` to include Engine Kill (Titan) missions in the primary mission pool. Defaults to `false`. |
| `twist` | No | Set to `true` to add a random narrative twist to the mission. Defaults to `false`. |
| `max_secondary_vp` | No | Maximum VP per secondary objective. Defaults to 4. Must be at least 1. |

Examples:

```text
/mission random
/mission random deployment:dawn-of-war
/mission random titans:true twist:true
/mission random max_secondary_vp:2
```

Response visibility: ephemeral. Only the user who ran the command sees the result.

Notes:

- Available deployment zone slugs: `ambush`, `annihilation`, `clash-of-the-line`, `dawn-of-war`, `death-or-glory`, `hammer-and-anvil`, `hold-at-all-costs`, `search-and-destroy`, `vanguard-strike`.
- If an unrecognised `deployment` slug is given, the bot replies with the available options.
- Secondary objective VP values are chosen randomly in the range 1–`max_secondary_vp` for each objective.
- The response includes a 3-minute **Reroll** button. Only the user who generated the mission can use it, and it preserves the original options.
- The response does not call an external LLM or API.
- Responses are trimmed to Discord's 2000-character message limit.

Permissions:

Unrestricted — any server member can use this command.

### `/lore random`

Creates a public random lore hook from the Markdown summaries. It can use the whole archive or focus on a faction, legion, warband, or force.

| Option | Required | Description |
| --- | --- | --- |
| `faction` | No | Optional faction, legion, warband, or force to focus the lore hook. If omitted, the bot samples from the general archive. |

Examples:

```text
/lore random
/lore random faction:Imperial Fists
/lore random faction:Night Lords
```

Response visibility: public, so the generated lore hook appears in the channel.

Notes:

- The command does not call an external LLM or API.
- It samples narrative lore only — the `rules/` and `missions/` summaries are excluded so the hook never returns rules text or a mission objective. Those live behind `/rules query` and `/mission random`.
- The summaries directory is configured with `SUMMARIES_DIR`; it is validated against the expected local and container summaries roots at startup.
- The response includes a title, focus, source path, lore cue, campaign prompt, and table hook.
- If `faction` is supplied but no matching Markdown summary is found, the bot sends an ephemeral error to the user.
- Responses are trimmed to Discord's 2000-character message limit.

Permissions:

Unrestricted — any server member can use this command.

### `/lore search`

Searches the Markdown summaries and returns the best matching excerpts with source paths.

| Option | Required | Description |
| --- | --- | --- |
| `query` | Yes | Search text, for example a faction, campaign, character, location, or theme. |

Examples:

```text
/lore search query:Imperial Fists siege
/lore search query:Calth betrayal
/lore search query:Blackshields
```

Response visibility: public, so the search results appear in the channel.

Notes:

- The command does not call an external LLM or API.
- It excludes `missions/` summaries so mission objectives do not appear as lore hits. Rules summaries can still appear in search results; use `/rules query` for a rules-first answer.
- The summaries directory is configured with `SUMMARIES_DIR`; it is validated against the expected local and container summaries roots at startup.
- Results prefer content Markdown files over `README.md` index files.
- The response includes ranked source paths and matching excerpts.
- Responses are trimmed to Discord's 2000-character message limit.

Permissions:

Unrestricted — any server member can use this command.

### `/source`

Looks up the most relevant summary source files for a lore query. Use this when someone needs to trace a faction, campaign, location, or theme back to the local Markdown archive.

| Option | Required | Description |
| --- | --- | --- |
| `query` | Yes | Search text, for example a faction, campaign, character, location, or theme. |

Examples:

```text
/source query:Thramas Crusade
/source query:Solar Auxilia
/source query:Signus
```

Response visibility: public, so the source lookup appears in the channel.

Notes:

- The command shares the same search logic as `/lore search`, including the `missions/` exclusion. Rules summaries can still appear in source results; use `/rules query` for a rules-first answer.
- The response is source-first: ranked file paths with a short cue for each match.
- Results prefer content Markdown files over `README.md` index files.
- Responses are trimmed to Discord's 2000-character message limit.

Permissions:

Unrestricted — any server member can use this command.

### `/rules query`

Searches the rules corpus (`summaries/rules/`) and returns the most relevant rules summary with a book/section citation and a source link. The bot never calls an external LLM — answers come entirely from the curated Markdown corpus, so answer quality grows as the corpus coverage grows.

| Option | Required | Description |
| --- | --- | --- |
| `query` | Yes | What rule you need, for example a keyword, action, weapon type, or short question. |

Examples:

```text
/rules query query:cover save
/rules query query:how does a charge work
/rules query query:perils of the warp
```

Response visibility: ephemeral — only the asker sees the answer, keeping channels clean.

Notes:

- The command does not call an external LLM or API.
- The top hit shows a longer excerpt under its topic title with a `Source: <book> — <section>` citation and the source URL. When there is more than one match, a dropdown lists every result — selecting one rewrites the message in place to show that rule's full explanation, so further matches are browsable rather than just listed. The dropdown is scoped to the asker and disables after three minutes.
- Scoring adds a frontmatter bonus on top of the body match: matches against a file's `keywords` and `rules_topic` rank higher.
- No matches returns a friendly "try rephrasing" message noting the corpus is still growing.
- Responses are trimmed to Discord's 2000-character message limit.
- New command: production needs one `/admin sync-commands` after the deploy that introduces it.

Permissions:

Unrestricted — any server member can use this command.

### `/feedback submit`

Logs a bug report or suggestion about the bot for the organisers to review. The submitter, the command the feedback is about, the sanitised comment, and a timestamp are stored in a per-guild JSON file on the VM (`FEEDBACK_STATE_PATH`, default `/app/state/feedback.json`), matching the club-night state files. The Claude `/feedback-actions` agent command reads that file and lists the open action points.

| Option | Required | Description |
| --- | --- | --- |
| `command` | Yes | Which command the feedback is about — a dropdown of member-facing commands plus an "Other / the bot in general" choice. |
| `comment` | Yes | The bug report or suggestion. Control/format characters are stripped, whitespace collapsed, and length capped at 1000 characters. |

Examples:

```text
/feedback submit command:/rules query comment:The excerpt for charges gets cut off.
/feedback submit command:Other / the bot in general comment:Could we get a /roll command?
```

Response visibility: ephemeral — only the submitter sees the confirmation, which echoes the stored comment back.

Notes:

- Comment text is sanitised on submission and escaped via `escape_public_text` wherever it is echoed back; the agent command also treats stored comments as untrusted data.
- Only the 500 most recent entries per guild are kept.
- New command: production needs one `/admin sync-commands` after the deploy that introduces it.

Permissions:

Unrestricted — any server member can use this command.

## Docker

Build and run the bot with Docker Compose:

```bash
cd discord-bot
cp .env.example .env
docker compose up --build -d
```

View logs:

```bash
docker compose logs -f
```

Deploy an update:

```bash
git pull
cd discord-bot
docker compose up --build -d
```

Stop the bot:

```bash
docker compose down
```

The compose file mounts `../summaries` into the container at `/app/summaries` as read-only content for future lore commands.

## Google Cloud Build

Continuous deployment to Google Cloud is configured in `cloudbuild.yaml`.

The deployment target is a GCE VM running the bot container under systemd, because the bot is a long-running Discord gateway process rather than an HTTP service. The bot is the only deployable; pushes to `development` test, promote, then deploy via `.github/workflows/deploy.yml`. Direct pushes to `main` and manual **Deploy bot** runs submit the same build.

The Dockerfile is written for `discord-bot/` as its build context. The Cloud Build config prepares its own temporary Docker context so it can also include the repository's root `summaries/` directory when the full repository is submitted.

The VM start script reads `DISCORD_TOKEN` and `DISCORD_GUILD_ID` from Secret Manager at service start. Optional owner allowlists can be configured with `BOT_OWNER_USER_IDS`. Command sync remains explicit through `/admin sync-commands`, `python -m bot sync-commands`, or a deliberate bootstrap deployment with `DISCORD_SYNC_COMMANDS_ON_STARTUP=true`.

See `docs/google-cloud-build.md` for the full setup.

## Layout

- `bot/__main__.py` starts the bot.
- `bot/config.py` reads environment configuration.
- `bot/commands/` holds slash command registration.
- `cloudbuild.yaml` builds and deploys the container on Google Cloud.
- `docs/architecture.md` captures design concerns and likely next steps.
