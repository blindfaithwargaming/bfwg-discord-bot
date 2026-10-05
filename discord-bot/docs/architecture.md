# Discord Bot Architecture

This repository can host the Discord bot source alongside the existing lore summaries, but the bot should stay isolated in `discord-bot/`. Treat `summaries/` as content/data owned by the wider repository.

## Starting Shape

- `bot/__main__.py` starts the Discord client, registers slash commands in memory, and only syncs them to Discord when explicitly requested.
- `bot/commands/` contains small command registration modules.
- `bot/config.py` reads local environment configuration, parses optional bot owner IDs, and validates `SUMMARIES_DIR`.
- `.env` stores local secrets and IDs. Commit `.env.example`, never `.env`.

## Architectural Concerns

- **Interaction model:** Prefer slash commands and Discord UI interactions over reading ordinary message text. This avoids needing Discord's privileged `MESSAGE_CONTENT` intent for most features.
- **Gateway intents:** Start with default intents. Add privileged intents only when a specific feature truly needs them.
- **Command sync:** Keep `DISCORD_SYNC_COMMANDS_ON_STARTUP=false` for production so old revisions cannot overwrite Discord's command tree during deployment. Use `/admin sync-commands`, `python -m bot sync-commands`, or a deliberate one-off `DISCORD_SYNC_COMMANDS_ON_STARTUP=true` bootstrap when slash command definitions change. Use `DISCORD_GUILD_ID` during development so explicit syncs target a test server quickly; leave it unset for global command sync once commands are stable.
- **Secrets:** Keep the bot token and production guild ID out of git. Rotate the bot token immediately if it is ever committed, shared, or logged.
- **Content access:** Treat `../summaries/` as read-only source material. `SUMMARIES_DIR` is validated at startup so public commands only read from expected local or container summaries roots.
- **Public output safety:** Public bot responses should use `discord.AllowedMentions.none()` and escape user-supplied display text before inserting it into Markdown-formatted messages.
- **Permissions:** Invite the bot with the minimum OAuth scopes and permissions needed for its commands. `/admin` commands use Discord default permissions plus a runtime administrator or `BOT_OWNER_USER_IDS` allowlist check.
- **Reliability:** Keep command handlers small, log exceptions, and move complex work into testable services.
- **Data storage:** A single bot instance keeps in-memory caches without locking. Durable local state (the club-night reminder dedup markers) is a JSON file on a mounted host volume. The bot makes no external database calls.
- **Deployment:** A gateway bot needs a long-running process. The local deployment path is Docker Compose; the Google Cloud deployment path is Cloud Build plus a GCE VM running the bot container under systemd. A push to `development` runs tests, promotes the tested commit to `main`, then deploys through `.github/workflows/deploy.yml`; direct `main` pushes and manual deploy runs use the same workflow.
- **Testing:** Keep lore lookup and scenario generation logic independent from Discord so it can be unit-tested without connecting to Discord.

## Container Notes

- The image contains only the bot code and Python dependencies.
- Cloud Build packages both the bot code and `summaries/` into the image.
- `compose.yaml` also mounts `../summaries` at `/app/summaries` as read-only content for local development.
- The container does not expose a port because it connects outbound to Discord.
- Secrets are supplied from `.env` via Compose, not baked into the image.
- On Google Cloud, `DISCORD_TOKEN` and `DISCORD_GUILD_ID` come from Secret Manager at VM service start. `BOT_OWNER_USER_IDS` is optional and may stay unset if Discord administrator permissions are enough. `DISCORD_SYNC_COMMANDS_ON_STARTUP` remains a normal deployment environment variable so sync bootstrap is an explicit deploy choice. The container mounts a host directory at `/app/state` for the reminder dedup markers.

## Shared Text Utilities

`bot/services/text_utils.py` exports `TOKEN_PATTERN`, `SENTENCE_PATTERN`, `DISCORD_MESSAGE_LIMIT`, `normalize_markdown`, `tokens`, `truncate`, and `best_excerpt(text, query_tokens, max_length)`. All lore and scenario service modules import from it rather than defining their own copies. `best_excerpt` accepts a `max_length` parameter so each service can keep its own context-appropriate excerpt length (260 for lore search, 220 for scenario seeds, 320 for random lore).

## Stadium Events Cache

`bot/services/stadium_events.py` uses a `StadiumEventsCache` mutable dataclass to hold the three in-process cache fields (`snapshot`, `club_night`, `club_night_at`). A single module-level `_cache` instance is the default, so production callers require no change. Both `get_events` and `get_next_club_night` accept an optional `cache` parameter; tests pass a fresh `StadiumEventsCache()` to get isolated state without poking at module globals.

## Club Night Commands

`bot/commands/club_night_announcement.py` registers two separate command entry points:

- `/post-club-night` — a top-level command that builds and publishes the announcement to the configured Discord forum channel.
- A `/club-night` group (registered via `register_club_night_commands`) with three subcommands:
  - `next` — fetches the next club night and runs the stadium conflict check, then returns an ephemeral summary without touching any forum channel.
  - `preview` — builds the full announcement content (title, template body, and stadium status) and returns it as an ephemeral message so an admin can verify it before committing to a public post.
  - `remind` — posts the reminder template to the current channel, using existing organised play forum thread titles as the date source.

`next` and `preview` are admin-only helpers for organisers. The member-facing sign-up
subcommands live in `bot/commands/club_night_pairings.py`, with `/club-night signups`
restricted to admins so the community command-list post only documents the self-service
sign-up, withdrawal, and preference commands.

The announcement content path lives in `bot/services/club_night_announcement.py` as `build_announcement_content(settings, refresh=...)` — an async helper that calls `get_next_club_night`, `build_announcement_title`, `load_announcement_body`, `get_stadium_status_lines`, and `append_stadium_status` in sequence, returning `(club_night, title, body)`. Both `post_club_night` and `club_night_preview` call this helper, so the content-building path is never duplicated. `club_night_next` uses only `get_next_club_night` and `get_stadium_status_lines` because it does not need the template body. The forum-tag lookup (`get_applied_tags`) lives in the same service so the announcement scheduler shares it.

Reminder commands deliberately do not use the Battlefields ticketing page for the event
date, because same-day ticket sales can disappear from that page. `bot/services/club_night_forum.py`
scans active and recent archived threads in `CLUB_NIGHT_FORUM_CHANNEL_ID`, parses titles
like `Organised Play - 11th June`, and returns the next non-past forum-posted club night.
The automatic scheduler and `/club-night remind` share that Discord-source lookup;
`/post-club-night`, `/club-night next`, and `/club-night preview` continue to use the
Battlefields page as the source of truth for creating/checking the main announcement.

## Club Night Pairing Data Model

Pairing is built from four independent local JSON stores plus one pure generator,
each keyed `guild_id → event_date (ISO)`:

- **`SignupStore`** (`club_night_signups.py`) — one entry per member per event:
  `display_name`, `signed_up_at`, `side` (`loyalist`/`traitor`/`either`),
  `preferred_user_id` (opponent request, honoured only when mutual), and
  `format_pref` (`solo`/`doubles`/`either`). `format_pref` is the doubles opt-in:
  `solo` (the default) is never given a teammate but may face a team, `doubles`
  is teamed eagerly, `either` is teamed only when it improves the night. Legacy
  entries without the field read back canonically as `solo` via
  `normalize_format_pref`. The read boundary also canonicalises malformed or
  legacy `side` and `preferred_user_id` values, so the generator receives one
  stable shape. Re-signing updates the known fields without dropping unknown
  entry fields, allowing later schema additions to survive older update paths.
  Per-field setters (`set_side`, `set_format`, `set_preference`) mutate a single
  key in place, so an organiser can adjust one player without the re-signup path
  bumping `signed_up_at` and reordering the list; they are the pattern any future
  field's mutation should follow. `set_side`/`set_format` back the admin
  `/club-night set-side` and `/club-night set-format` commands.
- **`PairingConstraintsStore`** (`club_night_pairing_constraints.py`) — organiser
  `locked_pairs`, `avoided_pairs`, and a `forced_bye`. Pairs are stored as sorted
  two-player lists; locks and forced byes are authoritative, so a corrupt store
  raises rather than silently pairing with no constraints.
- **`PairingHistoryStore`** (`club_night_pairing_history.py`) — the last
  `PAIRING_HISTORY_RETENTION_EVENTS` nights of posted **opponent pairs** (sorted
  two-player lists). `get_recent_opponents` unions the lookback window into a
  `frozenset[frozenset[str]]` feeding soft avoidance.
- **`generate_pairings`** (`club_night_pairings.py`) is pure: `signups + rng +
  constraints → PairingResult`.

### Match / PairingResult

A `Match` has two opposing sides, each a **sorted tuple of user ids of length ≥1**.
Construction requires ordered sequence inputs, canonicalises them to tuples,
enforces non-blank distinct players, and rejects a player appearing on both sides.
Players are sorted *within* each side, while side A/B order is preserved so a
future result or faction-specific field can refer to a stable side without another
model migration. A 1v1 is two length-one sides; doubles simply lengthens a side,
so 2v1, 2v2, and any future format (3v3, handicap games) need no structural
change — the two-sided shape is intrinsic, the sizes are open.
`Match.opponent_pairs()` yields only the cross-side pairs (teammates are not
opponents), and that decomposition — not the raw sides — is what feeds pairing
history and recent-opponent avoidance, so avoidance is identical to the 1v1 era
and indifferent to who shared a side. `PairingResult` canonicalises ordered
sequences of `matches` and `byes` to tuples, rejects unordered/bare-string
containers, and enforces that every participant appears exactly once across both.

### Auto-teaming (generation order)

`generate_pairings` resolves fixed 1v1 obligations first — `forced_bye`, then
`locked_pairs`, then mutual preferences — then groups everyone left into *units*
(a lone player, or a 2-player team) and matches units across sides:

1. **Unit formation** (`_form_units`) teams doubles-willing players. Teammates
   always share a faction side (two Loyalists, two Traitors, or with an "either"
   player — never Loyalist+Traitor). Keen (`doubles`) players are paired first; a
   lone keen player pulls in a compatible `either` partner. A final **parity
   guard** keeps the unit count even where possible: if it is odd it adds a team
   from two doubles-willing singletons, or rearranges one existing team with two
   willing singletons into two compatible teams. Both choices prioritise explicit
   `doubles` requests over flexible `either` players. Only when neither is
   possible does it dissolve one team back to 1v1s. Avoiding a bye therefore
   outranks honouring a doubles request — nobody is stranded to keep a team
   together.
2. **Unit matching** (`_match_units`) mirrors the 1v1 stage: Loyalist units cross
   against Traitor units, then leftovers pair off. A team facing a lone opponent
   is a 2v1; two teams are a 2v2. Recent/avoided opponents are a soft constraint
   applied per anchor (best-effort — it prefers a conflict-free opponent unit and
   falls back only when none exists). Any single leftover unit becomes the bye(s).

For all-`solo` sign-ups every unit is a singleton and the result is byte-for-byte
the pre-doubles behaviour, so the feature is strictly opt-in.

## Club Night Schedulers

`bot/services/club_night_scheduler.py` runs two ticks in one hourly background loop
(started from `on_ready`, independently enabled by config):

- **Announcement tick** — creates the next organised play forum post automatically. It
  derives the previous club night from existing forum post titles
  (`find_latest_announcement_club_night`, which parses past dates too), becomes due at
  `CLUB_NIGHT_ANNOUNCEMENT_TIME` (default 13:00 local) the day after, then reads the
  next event date from the Battlefields page and creates the forum post with the shared
  template/stadium-status/tag helpers. While a current or future announcement exists the
  tick skips, so a manual `/post-club-night` pre-empts the scheduler. If Battlefields
  does not list the next event yet, the tick reports `next_club_night_unavailable` and
  retries on later ticks — the due condition is "from the due time onwards", not a
  single-day window, so downtime is caught up.
- **Reminder tick** — posts the reminder `CLUB_NIGHT_REMINDER_DAYS_BEFORE` days before
  the forum-posted club night at or after `CLUB_NIGHT_REMINDER_TIME`.

Both ticks dedup through `MarkerStore` markers keyed `club-night-reminder-<date>` /
`club-night-announcement-<date>` (shared `claim_scheduled_post_marker` /
`mark_scheduled_post_posted` / `mark_scheduled_post_failed` helpers with a `kind`
discriminator and a 3-attempt retry cap).

## Random Lore Single-Pass File Scan

`bot/services/random_lore.py` reads each Markdown file exactly once. The private `_collect_entries(root, markdown_files, tokens)` function checks token presence against the `path + text` haystack and extracts candidate excerpts in the same loop iteration. It returns `(token_matches, entries)` so `generate_random_lore` can distinguish "no files matched the token filter" (raises "No Markdown summaries matched…") from "no readable excerpts found" (raises "No readable Markdown summaries found…").

## Implemented Lore Features

- `/scenario seed` creates a short mission seed from attacker, defender, and optional tone inputs. It scans `summaries/` for matching Markdown source cues and formats the result for Discord without needing an external LLM or API key.
- `/lore random` creates a short lore hook from the Markdown summaries. It can sample the general archive or filter by an optional faction/focus, then returns a source cue, campaign prompt, and table hook without needing an external LLM or API key.
- `/lore search` searches Markdown summaries and returns ranked excerpts with source paths.
- `/source` uses the same search service to return source-first results for a lore query.
- Public lore, source, and scenario responses escape user-controlled display text and disable mention parsing.

## Feature Backlog

- `/roll` dice command for quick table utility.
- Scheduled lore post using the existing reminder scheduler and `MarkerStore` duplicate protection.
