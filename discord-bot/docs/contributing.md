# Contributing: Adding New Commands

How to add a new slash command to the BFWG bot from scratch to deployed.

---

## Prerequisites

From `discord-bot/`:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
# Add your DISCORD_TOKEN and DISCORD_GUILD_ID to .env
```

Run the tests to confirm everything is working:

```bash
pytest
```

The whole suite is pure-Python against `tmp_path` — no emulator, cloud SDKs, network, or
Discord token needed. (An earlier analytics platform required the Firestore emulator and
dashboard runtime deps; that code is parked in `archive/analytics-platform/` and is not part
of the live test run.)

---

## Codebase Map

```
bot/
  services/       ← business logic (pure Python, no Discord imports)
  commands/       ← thin Discord interaction handlers only
  commands/__init__.py   ← register_commands() wires everything together
  commands/interaction_helpers.py  ← command_context, check_rate_limit, defer_ephemeral
  commands/discord_safety.py       ← NO_MENTIONS, escape_public_text
tests/            ← pytest suite; one test file per service module
```

See [architecture.md](architecture.md) for design decisions and [operations.md](operations.md) for deployment.

---

## The Full Flow

Every new command follows these steps in order.

### 1. Write the service module

Create `bot/services/<name>.py`. All business logic lives here — no Discord imports.

Expose:
- A typed result dataclass or named return type.
- A domain-specific exception class so callers can distinguish "expected failure" from unexpected errors.
- At least one public function that accepts a `summaries_dir: str` or other plain inputs and returns the result.
- An injectable `rng` parameter on any function that uses randomness — `random.Random | None = None`. Use `random.SystemRandom()` as the default so tests can pass a seeded `random.Random` for determinism.
- A `format_*` function if the output needs to be Discord-formatted.

### 2. Write the command module

Create `bot/commands/<name>.py`. Import from your service; keep all Discord plumbing here and nothing else.

Every handler body follows the same fixed order:

```python
async with command_context(interaction, "command-name"):
    if not await check_rate_limit(interaction, "command-name"):
        return
    if not await defer_ephemeral(interaction, "command-name"):
        return
    # ... call service, send followup
```

- `command_context` — always first; provides audit logging.
- `check_rate_limit` — always second; enforces the per-user 3-second cooldown.
- `defer_ephemeral` — always third; acknowledges the interaction and sets the loading state.

Skip `check_rate_limit` only for admin-only commands where rate limiting is not meaningful. Never skip `command_context`.

Public responses that display user-supplied text must call `escape_public_text()` on that text and pass `allowed_mentions=NO_MENTIONS` to every `followup.send`.

Expose a single `register_<name>_command(tree)` function (or `register_<name>_commands` for a command group).

### 3. Register in `__init__.py`

In `bot/commands/__init__.py`:

```python
from bot.commands.<name> import register_<name>_command

def register_commands(tree: discord.app_commands.CommandTree) -> None:
    ...
    register_<name>_command(tree)
```

### 4. Write tests

Create `tests/test_<name>.py`. Test the service functions directly — do not import Discord or instantiate the bot.

Key rules:
- Wrap async test bodies in a `def` that calls `asyncio.run(...)` — no `pytest-asyncio`.
- Use `tmp_path` (a built-in pytest fixture) for any filesystem state.
- Use the shared `summaries_dir` fixture from `conftest.py` if your tests need a populated summaries directory.
- Use the autouse `reset_rate_limiter` fixture from `conftest.py` if your tests touch rate-limit state.
- Pass a seeded `random.Random` to any service function that accepts `rng` to make assertions deterministic.
- Test the format function for Discord message length: ensure output stays within `DISCORD_MESSAGE_LIMIT` (2000 chars) even with the largest plausible inputs.

Run the full suite to check for regressions:

```bash
pytest
```

### 5. Update README.md

Add a section under **Commands** in `discord-bot/README.md`:

```markdown
### `/your-command`

One-line description of what it does.

**Parameters**

| Parameter | Required | Description |
| --- | --- | --- |
| `param` | Yes | What it accepts. |

**Permissions**

Unrestricted — any server member can use this command.
```

If the command has restricted permissions (e.g. `manage_threads`), document that instead.

### 6. Sync commands

After merging, the bot's in-memory command tree is correct but Discord does not yet know about the new command. Sync explicitly — do not rely on `DISCORD_SYNC_COMMANDS_ON_STARTUP`.

If the bot is running and you have admin access in Discord:

```
/admin sync-commands
```

Or run the one-shot sync locally with the production token in `.env`:

```bash
python -m bot sync-commands
```

If `DISCORD_GUILD_ID` is set, the command appears in that server within seconds. Global sync (no `DISCORD_GUILD_ID`) can take up to an hour.

---

## Worked Example: `/roll`

A minimal command that rolls a die.

### Service: `bot/services/dice.py`

```python
import random


class DiceError(Exception):
    pass


def roll_die(sides: int, rng: random.Random | None = None) -> int:
    if sides < 2:
        raise DiceError(f"A die must have at least 2 sides, got {sides}.")
    r = rng or random.SystemRandom()
    return r.randint(1, sides)
```

### Command: `bot/commands/dice.py`

```python
import discord
from discord import app_commands

from bot.commands.discord_safety import NO_MENTIONS
from bot.commands.interaction_helpers import check_rate_limit, command_context, defer_ephemeral
from bot.services.dice import DiceError, roll_die


def register_dice_command(tree: discord.app_commands.CommandTree) -> None:
    @tree.command(name="roll", description="Roll a die.")
    @app_commands.describe(sides="Number of sides on the die (default 6).")
    async def roll(interaction: discord.Interaction, sides: int = 6) -> None:
        async with command_context(interaction, "roll"):
            if not await check_rate_limit(interaction, "roll"):
                return
            if not await defer_ephemeral(interaction, "roll"):
                return

            try:
                result = roll_die(sides)
            except DiceError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            await interaction.followup.send(
                f"Rolled a d{sides}: **{result}**",
                allowed_mentions=NO_MENTIONS,
            )
```

### Registration: `bot/commands/__init__.py`

```python
from bot.commands.dice import register_dice_command

def register_commands(tree: discord.app_commands.CommandTree) -> None:
    ...
    register_dice_command(tree)
```

### Tests: `tests/test_dice.py`

```python
import random
import pytest
from bot.services.dice import DiceError, roll_die


def test_roll_die_returns_value_in_range() -> None:
    rng = random.Random(42)
    for _ in range(50):
        result = roll_die(6, rng=rng)
        assert 1 <= result <= 6


def test_roll_die_covers_full_range() -> None:
    rng = random.Random(0)
    results = {roll_die(6, rng=rng) for _ in range(200)}
    assert results == {1, 2, 3, 4, 5, 6}


def test_roll_die_raises_on_single_side() -> None:
    with pytest.raises(DiceError, match="at least 2 sides"):
        roll_die(1)


def test_roll_die_raises_on_zero_sides() -> None:
    with pytest.raises(DiceError):
        roll_die(0)
```

### README entry

```markdown
### `/roll`

Roll a die with the given number of sides.

**Parameters**

| Parameter | Required | Description |
| --- | --- | --- |
| `sides` | No | Number of sides (default 6). Minimum 2. |

**Permissions**

Unrestricted — any server member can use this command.
```

---

## Branching and review

Feature work does **not** go straight onto `development`. The flow is:

1. Cut a branch from `development`: `git switch development && git pull && git switch -c feature/<slug>`.
2. Do the work on that branch and push it: `git push -u origin feature/<slug>`.
3. Open a PR **into `development`** (`gh pr create --base development`). The **Test Python
   bot** check runs on the PR.
4. The owner reviews the diff and merges. Agents do not push to `development` or `main` and
   do not merge their own PRs. (Server-side branch protection on `development` is the
   intended hard gate but needs GitHub Pro for this private repo — see
   `operations.md`; until then the gate is convention plus the PR check.)
5. Merging the PR pushes `development`, which re-runs tests, fast-forward-promotes the tested
   commit to `main`, and triggers **Deploy bot** → Cloud Build → VM. Nothing else to do.

## Design Rules Checklist

Before opening a PR into `development`, check each of these:

- [ ] No business logic in `bot/commands/` — it all lives in `bot/services/`.
- [ ] Handler starts with `command_context` → `check_rate_limit` → `defer_ephemeral` → logic.
- [ ] User-supplied text in public responses is passed through `escape_public_text()`.
- [ ] Every `followup.send` passes `allowed_mentions=NO_MENTIONS`.
- [ ] Tests import only from `bot/services/`, not from `bot/commands/`.
- [ ] Async test bodies wrapped in `asyncio.run()` — no `pytest-asyncio`.
- [ ] `tmp_path` used for filesystem state in tests.
- [ ] Functions with randomness accept an injectable `rng` parameter.
- [ ] Format function tested for message length (output ≤ 2000 chars).
- [ ] README updated with a command section.
- [ ] `RECOMMENDATIONS.md` updated if an outstanding work item was completed.

---

## Working with agents

This repo is set up so coding agents (Claude Code, Codex, etc.) can pick up work across
sessions. In the private repository the entry point for any agent is `AGENTS.md` at the repo root;
`CLAUDE.md` holds the full project context and design rules.

The moving parts:

- **Skills** — `load-handover` (start-of-session briefing) and `handover` (end-of-session
  wrap-up). Claude Code versions live in `.claude/commands/`; cross-agent versions in
  `.agents/skills/`. Run `load-handover` at the start of a session and `handover` at the end.
- **Memory** — `.agent-memory/` at the repo root is the git-tracked, cross-agent memory
  (index: `.agent-memory/MEMORY.md`). Claude Code also keeps its own auto-memory outside the
  repo; its `handover` skill mirrors session state into `.agent-memory/` so both stay current.
- **`RECOMMENDATIONS.md`** — the single source of truth for outstanding and completed work.
  Agents update it whenever an item is finished; "recommendations" and "suggestions" are
  synonyms.
- **`.claude/settings.json`** — read-only `gcloud`/`gh` allowlist so agents can monitor CI
  runs and deploys without permission prompts. Mutating commands always prompt.

If agent-visible state changes outside a normal handover (architecture shifts, test count,
deploy process), refresh `.agent-memory/project_state.md` — stale memory is worse than none.
