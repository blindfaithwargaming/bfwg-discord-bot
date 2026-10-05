# Operations Runbook

The BFWG bot is a single deployable: one Docker image running on one GCE VM under systemd.
Telemetry goes to Cloud Logging; reminder dedup state is a local JSON file. There is no
Firestore, Pub/Sub, ingestor, or dashboard (those are parked in the private repository's
`archive/analytics-platform/`).

---

## Resources

| Resource | Value |
|---|---|
| Bot VM | `bfwg-discord-bot`, zone `us-central1-a`, spec **`e2-micro`** (free tier) |
| Service account | `bfwg-discord-bot@$PROJECT_ID.iam.gserviceaccount.com` |
| Artifact Registry | repo `bfwg`, region `us-central1` (colocated with the VM) |
| Secrets | `discord-token`, `guild-id` (Secret Manager) |
| Reminder state | `/var/lib/bfwg/state/scheduled_posts.json` on the VM (mounted at `/app/state`) |
| Sign-up state | `/var/lib/bfwg/state/club_night_signups.json` on the VM (same mount) |
| Pairing history | `/var/lib/bfwg/state/club_night_pairing_history.json` on the VM (same mount) |
| Pairing constraints | `/var/lib/bfwg/state/club_night_pairing_constraints.json` on the VM (same mount) |
| Feedback | `/var/lib/bfwg/state/feedback.json` on the VM (same mount) |
| Game organisation export | `/var/lib/bfwg/state/game_organisation_export.json` on the VM (same mount) |
| Club night games | `/var/lib/bfwg/state/club_night_games.json` on the VM (same mount) |
| State mirror bucket | `gs://blind-faith-494118-bfwg-bot-state` (us-central1, Standard, free tier) |

**Bot SA IAM roles:** `roles/secretmanager.secretAccessor`, `roles/artifactregistry.reader`,
`roles/logging.logWriter`, plus `roles/storage.objectUser` granted **on the state mirror
bucket only** (a bucket-level binding, not project-level). (The old `roles/datastore.user`
and `roles/pubsub.publisher` are no longer needed — remove them.)

---

## Deploy

Feature work lands via a PR from a `feature/<slug>` branch into `development`; until branch
protection can be enabled, this is enforced by convention plus the **Test Python bot** PR
check. Merging the PR pushes `development`, which runs
`.github/workflows/discord-bot-tests.yml`. When tests pass, it fast-forwards `main` to the
tested commit; `.github/workflows/deploy.yml` then runs from `workflow_run` and submits Cloud
Build. Direct pushes to `main` and manual **Deploy bot** runs also submit the same build.

Every `development` push that passes tests deploys, including docs-only changes. This is
intentional: the VM always runs exactly what `main` points at, with no partially-deployed
drift to reason about. The workflow's `deploy-bot` concurrency group prevents
over-deployment — runs queue rather than overlap, and a burst of pushes collapses to at
most one queued deploy behind the running one. The cost is one Cloud Build run and a brief
bot restart per push, well within the free tier at this project's push volume.

### Branch protection on `development`

The review gate is meant to be a branch-protection rule on `development`: a PR is required to
merge, the **Test Python bot** status check must pass, and admins (the owner) are exempt so
they can still push in an emergency.

**Not yet enabled.** GitHub gates both classic branch protection and repository rulesets
behind **GitHub Pro** (or making the repo public) for private repositories — the API returns
`403 Upgrade to GitHub Pro or make this repository public`. Until the repo is on Pro or made
public, the `feature/<slug>` → PR → `development` flow is enforced by convention (docs + the
PR test check) only; nothing server-side blocks a direct push to `development`.

Once Pro is active (or the repo is public), apply a ruleset — free for private repos on Pro
and the preferred mechanism:

```bash
gh api -X POST repos/blindfaithwargaming/bfwg/rulesets --input - <<'JSON'
{
  "name": "development review gate",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/development"], "exclude": [] } },
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
  ],
  "rules": [
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false, "require_last_push_approval": false,
        "required_review_thread_resolution": false } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [ { "context": "Test Python bot" } ] } }
  ]
}
JSON
```

`required_approving_review_count: 0` requires a PR but no formal approval — the owner reviews
the diff and merges; `actor_id: 5` (the admin repository role) keeps the owner's bypass.
`main` is intentionally left unprotected so the automated fast-forward promotion (which
pushes with `GITHUB_TOKEN`) is never blocked.

Cloud Build builds and pushes the single bot image and reconfigures the VM over SSH
(`discord-bot/deploy/start-bot.sh` + the systemd unit), then restarts the service with the
immutable `$BUILD_ID` tag.

The GitHub Actions deploy submits the GitHub repo URL at the exact tested commit SHA instead
of uploading the checked-out workspace. That avoids the legacy Cloud Build source staging
bucket (`gs://${PROJECT_ID}_cloudbuild`), which WIF-submitted builds can be blocked from
using by project policy or missing quota-project permissions.

Manual deploy from a local checkout still uploads the current working tree:

```bash
gcloud builds submit --config discord-bot/cloudbuild.yaml .
```

Substitutions default for production. Override only for a deliberate test or emergency
config change, such as a same-day scheduler verification with
`_CLUB_NIGHT_REMINDER_DAYS_BEFORE=0,_CLUB_NIGHT_REMINDER_TIME=15:00`.

### One-time: Workload Identity Federation for the deploy workflow

The `deploy.yml` workflow authenticates with WIF (no long-lived key). Once per project:

1. Create a workload identity pool + provider for GitHub Actions, restricted to this repo.
2. Create/choose a deploy service account; grant it `roles/cloudbuild.builds.editor`
   and `roles/serviceusage.serviceUsageConsumer` (`gcloud builds submit` needs
   `serviceusage.services.use`).
3. Grant the deploy SA `roles/iam.serviceAccountUser` **on the service account Cloud Build
   runs builds as** — in this project that is the Compute Engine default SA, since
   `cloudbuild.yaml` does not pin a `serviceAccount:`. Submitting a build requires
   `iam.serviceAccounts.actAs` on that SA; without it the submit fails with
   "caller does not have permission to act as service account" (see troubleshooting below).
   Cloud Build's own SA performs the privileged Artifact Registry push and VM SSH steps.
4. Grant the deploy SA `roles/storage.admin` **on the staging bucket only**
   (`gs://${PROJECT_ID}_cloudbuild`). The workflow uploads the checked-out workspace, and
   the upload needs bucket get/write, which project-level `objectAdmin` lacks. The workflow
   must also pass `--gcs-source-staging-dir` explicitly: without it, gcloud verifies bucket
   ownership by listing the project's buckets (`storage.buckets.list`), which a
   bucket-scoped grant cannot satisfy — the submit fails with "The user is forbidden from
   accessing the bucket" even though the SA can read and write it.

   ```bash
   gcloud storage buckets add-iam-policy-binding gs://${PROJECT_ID}_cloudbuild \
     --member="serviceAccount:$GCP_DEPLOY_SA" \
     --role="roles/storage.admin"
   ```
5. Bind the GitHub principal to the deploy SA with `roles/iam.workloadIdentityUser`.
6. Set repo **variables** `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`.

The deploy workflow asks `google-github-actions/auth` for a short-lived OAuth access token
and passes it to `gcloud builds submit` with `--access-token-file`, uploading the workspace
checked out at the tested commit SHA. Passing the GitHub repo URL as a git source does
**not** work: the repo is private, so Cloud Build's anonymous fetch fails with
`source fetch container exited with non-zero status: 128` (it would need a Cloud Build repo
connection). The root `.gcloudignore` keeps `archive/` and the mission source PDFs out of
the upload. If the workflow instead reports `Unable to retrieve Identity Pool subject token`,
rerun once after a few minutes; if it repeats, the failure is in the GitHub OIDC/WIF token
exchange before gcloud can submit the build, not in the Cloud Build YAML.

If `gcloud builds submit` fails in GitHub Actions and mentions `serviceusage.services.use`,
the deploy service account is missing Service Usage Consumer or the role was granted to the
wrong account:

```bash
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$GCP_DEPLOY_SA" \
  --role="roles/serviceusage.serviceUsageConsumer" \
  --condition=None
```

If it fails with `caller does not have permission to act as service account
projects/$PROJECT_ID/serviceAccounts/<numeric-unique-id>`, the deploy SA lacks actAs on the
build service account. Identify which SA the unique ID belongs to, then grant
`serviceAccountUser` on that SA (not project-wide):

```bash
gcloud iam service-accounts list --format="table(email,uniqueId)"
gcloud iam service-accounts add-iam-policy-binding <build-sa-email> \
  --member="serviceAccount:$GCP_DEPLOY_SA" \
  --role="roles/iam.serviceAccountUser"
```

(If WIF setup is too fiddly for now, a service-account JSON key in a repo secret is the simpler
fallback — swap the `auth` step inputs accordingly.)

---

## Free-tier guardrails

**Artifact Registry cleanup policy** (free tier is 0.5 GB — without this, retained image tags
grow unbounded and start billing):

```bash
gcloud artifacts repositories set-cleanup-policies bfwg \
  --location=us-central1 \
  --policy=- <<'JSON'
[
  {"name": "keep-latest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}},
  {"name": "delete-old-untagged", "action": {"type": "Delete"},
   "condition": {"tagState": "untagged", "olderThan": "604800s"}}
]
JSON
```

**VM spec:** confirm the VM is `e2-micro` in `us-central1-a` (only that spec + a free-tier
region — `us-central1`/`us-east1`/`us-west1` — is free):

```bash
gcloud compute instances describe bfwg-discord-bot --zone=us-central1-a \
  --format="value(machineType.scope(machineTypes))"
```

**Region:** Artifact Registry is `us-central1`, colocated with the VM, so image pulls stay
in-region. One-time creation of the colocated repo (the old `europe-west2` repo is torn down
below):

```bash
gcloud artifacts repositories create bfwg \
  --location=us-central1 --repository-format=docker \
  --description="BFWG bot images"
```

**State mirror bucket:** the GCS always-free tier is 5 GB of Standard-class storage in
`us-central1`/`us-east1`/`us-west1`, plus 5,000 Class A and 50,000 Class B operations per
month. The mirrored state files are kilobytes and uploads are mtime-gated (plus one
re-seed per bot restart), so this feature uses tens of Class A operations a month —
nowhere near the limit. Keep the bucket in `us-central1` and Standard class.

---

## Teardown of parked analytics resources (one-time) — executed 2026-06-11

The teardown is complete: every resource below is gone from GCP, along with the
trigger-era leftovers (`github-actions-cloudbuild` SA, `bfwg-github-oauthtoken-73b65d`
secret, the empty Firestore `bfwg` database, the disabled `ingethel-1` trigger). Commands
kept for reference in case the analytics platform is ever revived and re-torn-down:

```bash
# Old europe-west2 Artifact Registry repo (replaced by the colocated us-central1 repo,
# once at least one image has been pushed to us-central1 and a deploy verified)
gcloud artifacts repositories delete bfwg --location=europe-west2 --quiet

# Cloud Run services
gcloud run services delete bfwg-telemetry-ingestor --region=europe-west2 --quiet
gcloud run services delete bfwg-dashboard --region=europe-west2 --quiet

# Pub/Sub
gcloud pubsub subscriptions delete bot-telemetry-to-ingestor --quiet
gcloud pubsub topics delete bot-telemetry bot-telemetry-dead-letter --quiet

# Service accounts
for SA in bfwg-telemetry-ingestor bfwg-pubsub-push bfwg-dashboard; do
  gcloud iam service-accounts delete "$SA@$PROJECT_ID.iam.gserviceaccount.com" --quiet
done

# Dashboard secrets
gcloud secrets delete dashboard-secret-key --quiet
gcloud secrets delete discord-client-secret --quiet

# Drop now-unused roles from the bot SA
for ROLE in roles/datastore.user roles/pubsub.publisher; do
  gcloud projects remove-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:bfwg-discord-bot@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="$ROLE"
done
```

The empty Firestore `bfwg` database is free to leave in place; delete it from the console if
you prefer a clean project.

---

## Logs and health

- **Container logs (Cloud Logging):** the VM runs Docker with the `gcplogs` driver, so logs
  appear under the VM resource in Logs Explorer. Startup emits structured lines: guild target,
  summaries count, forum channel, templates, stadium/Battlefields URLs.
- **On the VM:** `sudo systemctl status bfwg-discord-bot`, `sudo journalctl -u bfwg-discord-bot -f`,
  `docker logs bfwg-discord-bot`.
- **In Discord:** `/admin diagnostics` (bot user, summaries count, command count, forum channel,
  commit SHA, stadium cache age) and `/admin config-check` (per-check pass/fail).

## Rollback

Images are tagged with the build's `$BUILD_ID` and `latest`. To roll back, SSH to the VM and
restart `start-bot.sh` with a previous image URI:

```bash
sudo /etc/bfwg/start-bot.sh us-central1-docker.pkg.dev/$PROJECT_ID/bfwg/discord-bot:<previous-build-id>
```

Then re-sync commands if the rollback changes the command tree (`/admin sync-commands`).

## Command sync

`DISCORD_SYNC_COMMANDS_ON_STARTUP=false` in production. After changing slash command
definitions, sync explicitly with `/admin sync-commands` (or `python -m bot sync-commands`).

## Club-night schedulers

Production deploy config enables both schedulers; they share one hourly loop
(`CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS`).

**Announcement scheduler** (`CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED`): creates the
next organised play forum post automatically, replacing the routine manual
`/post-club-night` run. It is eligible from `CLUB_NIGHT_ANNOUNCEMENT_TIME` (default
`13:00` local) on the day after the most recent announced club night — derived from
existing forum post titles — and posts as soon as the Battlefields ticketing page lists
the next event (ticks log `next_club_night_unavailable` until it does). While a current
or future announcement post exists, ticks skip with `announcement_exists`, so a manual
`/post-club-night` simply pre-empts the scheduler. `/post-club-night` stays available
for off-cycle or corrected posts.

**Reminder scheduler** (`CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED`): posts the reminder to
the configured reminder channel. It derives the next club-night date from existing
Discord forum posts in `CLUB_NIGHT_FORUM_CHANNEL_ID`, looking for titles such as
`Organised Play - 11th June` — which the announcement scheduler (or `/post-club-night`)
creates.

Duplicate protection for both is a JSON marker file at `CLUB_NIGHT_REMINDER_STATE_PATH`
(`/app/state/scheduled_posts.json`) with `club-night-reminder-<date>` and
`club-night-announcement-<date>` keys, persisted on the VM via the `/var/lib/bfwg/state`
mount so it survives container restarts. When either scheduler is enabled, startup
validates that the state directory is writable and fails fast with a
`CLUB_NIGHT_REMINDER_STATE_PATH` error if the mount is missing or read-only — a
crash-looping container right after enabling a scheduler points there. To inspect or
reset:

```bash
sudo cat /var/lib/bfwg/state/scheduled_posts.json
sudo rm /var/lib/bfwg/state/scheduled_posts.json   # forces re-evaluation on next tick
```

**Club-night sign-ups** (`/club-night signup`/`withdraw`/`signups`/`prefer`/`pairings`)
keep a second state file on the same mount at `CLUB_NIGHT_SIGNUP_STATE_PATH`
(`/app/state/club_night_signups.json`). It is keyed by guild then club-night date, so a
new organised play forum post automatically starts a fresh sign-up list; entries older
than 60 days are pruned on write. Each member entry includes their faction side,
opponent preference, and doubles format preference (`solo`/`doubles`/`either`).
Legacy entries without a format preference read as `solo`. The file is safe to delete
between events — players just sign up again:

```bash
sudo cat /var/lib/bfwg/state/club_night_signups.json
sudo rm /var/lib/bfwg/state/club_night_signups.json   # clears all sign-ups
```

**Pairing history** (`/club-night pairings`) keeps a third state file on the same mount
at `CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH`
(`/app/state/club_night_pairing_history.json`). Each posted pairing is recorded per guild
and club-night date (last-write-wins), and the command avoids re-pairing opponents from
the last `CLUB_NIGHT_PAIRING_LOOKBACK` recorded nights (default 2). Only the 12 most
recent dates per guild are kept. Deleting the file only forgets pairing history — repeat
avoidance restarts from scratch and sign-ups are unaffected:

```bash
sudo cat /var/lib/bfwg/state/club_night_pairing_history.json
sudo rm /var/lib/bfwg/state/club_night_pairing_history.json   # resets repeat avoidance
```

**Pairing constraints** (the `/club-night` admin pairing commands) keep a fourth state
file on the same mount at `CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH`
(`/app/state/club_night_pairing_constraints.json`). It is keyed by guild and club-night
date and holds the organiser's locked pairs, avoided pairs, and forced bye for that night.
Both `/club-night pairings` and `/club-night preview-pairings` read it; `/club-night
clear-signups` clears it alongside the sign-ups. Entries are pruned 60 days after their
date. Deleting the file only forgets organiser constraints — sign-ups and history are
unaffected:

```bash
sudo cat /var/lib/bfwg/state/club_night_pairing_constraints.json
sudo rm /var/lib/bfwg/state/club_night_pairing_constraints.json   # clears all constraints
```

**Bot feedback** (`/feedback submit`) keeps a fifth state file on the same mount at
`FEEDBACK_STATE_PATH` (`/app/state/feedback.json`). It is keyed by guild and holds a
chronological list of member-submitted bug reports and suggestions — each entry records
the submitter, the command it is about (or `other`), the sanitised comment, a timestamp,
and a `status` (`open` by default). Only the 500 most recent entries per guild are kept.
The Claude `/feedback-actions` agent command reads this file **via the state mirror
bucket** (see "State sync to GCS" below). To inspect or clear it by hand — clearing is
**two** commands, because uploads only fire on writes, so removing only the VM file
leaves a stale bucket object that `/feedback-actions` would re-report:

```bash
sudo cat /var/lib/bfwg/state/feedback.json
sudo rm /var/lib/bfwg/state/feedback.json   # clears all logged feedback…
gcloud storage rm gs://blind-faith-494118-bfwg-bot-state/feedback.json   # …and its mirror
```

**Game organisation export** (the daily scheduler tick) keeps a sixth state file on the
same mount at `GAME_ORGANISATION_EXPORT_PATH` (`/app/state/game_organisation_export.json`).
It is a raw snapshot of every thread and message in the `GAME_ORGANISATION_CHANNEL_ID`
forum — full server history, no per-thread message cap, no faction interpretation —
refreshed when older than `GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS` (default 24). The
Claude `/faction-distribution` agent command reads this file **via the state mirror
bucket** (see "State sync to GCS" below) and works out the faction spread agent-side. **Prerequisite:** the bot requests the privileged Message Content
gateway intent, so the *Message Content Intent* toggle must be on in the Discord
Developer Portal (Bot → Privileged Gateway Intents) before deploying this feature —
otherwise the gateway refuses the connection. Deleting the file just forces a re-export
on the next hourly tick:

```bash
sudo cat /var/lib/bfwg/state/game_organisation_export.json
sudo rm /var/lib/bfwg/state/game_organisation_export.json   # forces a fresh export
```

**Club night games** (`/club-night report-game`) keeps a seventh state file on the same
mount at `CLUB_NIGHT_GAMES_STATE_PATH` (`/app/state/club_night_games.json`). It holds a
flat, versioned list of member-reported game results — per game: the club night, thread,
reporter, result, per-player side/faction/result, and optional points size, mission,
notes, and campaign hex. **It is never pruned — the file is the club's game-results history and the corpus
the Claude `/club-night-results` agent command analyses** (win rates, faction
performance) via the state mirror bucket (see "State sync to GCS" below). Individual bad
records are removed in Discord with `/club-night remove-game` (reporter or admin), so
`sudo rm` should only ever be needed to wipe the entire history deliberately (pair it
with a `gcloud storage rm` of the mirror object, as with feedback):

```bash
sudo cat /var/lib/bfwg/state/club_night_games.json
sudo rm /var/lib/bfwg/state/club_night_games.json   # wipes ALL recorded game results
```

---

## State sync to GCS (agent reads)

The three agent-facing state files — `club_night_games.json`, `feedback.json`, and
`game_organisation_export.json` — are mirrored to the private bucket
`gs://blind-faith-494118-bfwg-bot-state` so the Claude/Codex agent commands
(`/club-night-results`, `/feedback-actions`, `/faction-distribution`) read them with
`gcloud storage cat` instead of a root shell on the VM. SSH to the VM is a human/rollback
operation only; agents never need it.

**Design principle — member data stays private.** State containing member-submitted
content lives in exactly two places: the prod VM's state mount and this private bucket.
Access is IAM-scoped to explicitly permitted principals — currently the bot SA
(bucket-scoped `roles/storage.objectUser`, write side) and the shared BFWG Google
account (read side). No object is ever public (`--public-access-prevention` is enforced),
and no per-member IAM grants exist. Future features that handle member data must follow
this pattern: mirror to this bucket (or an equally private one), never widen access
casually, and never route agent reads through a VM shell.

**How it works.** `bot/services/state_sync.py` uploads a file straight after each
successful write (`/club-night report-game`/`remove-game`, `/feedback submit`), and an
hourly "state sync" scheduler tick backstops anything missed, running last in the pass so
a fresh forum export is uploaded seconds after it is written. Sync bookkeeping is
in-memory, so every bot restart re-uploads all files — that re-seeds the bucket and acts
as a live check of token, IAM, and bucket on each boot. Uploads authenticate via the VM
metadata server (no key files) and are **best-effort**: a failure is logged (WARNING from
the fire-and-forget hook, ERROR from the tick) and never fails the member's Discord
command. Config: `GCS_STATE_UPLOAD_ENABLED` + `GCS_STATE_BUCKET` (both wired through
`cloudbuild.yaml` → `start-bot.sh`).

**One-time setup** (already executed for this project; kept for re-creation):

```bash
gcloud storage buckets create gs://blind-faith-494118-bfwg-bot-state \
  --location=us-central1 --default-storage-class=STANDARD \
  --uniform-bucket-level-access --public-access-prevention

# objectUser (not objectCreator): overwriting an object needs storage.objects.delete.
gcloud storage buckets add-iam-policy-binding gs://blind-faith-494118-bfwg-bot-state \
  --member="serviceAccount:bfwg-discord-bot@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/storage.objectUser
```

**Verify:**

```bash
gcloud storage ls -L gs://blind-faith-494118-bfwg-bot-state/   # objects + updated times
```

Cloud Logging shows `GCS state upload: enabled (bucket=…)` at startup and a
`Scheduler state sync tick status=…` line each pass (`uploaded`/`skipped`/`failed`). A
`failed` tick or a `State sync upload failed` warning means token, IAM, or bucket trouble
— member commands keep working regardless, but agent commands will see stale data until
it is fixed (they print a "data as of" timestamp from the object metadata, so staleness
is visible). Deleting a bucket object alone does not heal until the next write or bot
restart, because the mtime bookkeeping is what triggers re-uploads.

**Audit logging:** GCS Data Access audit logs are off by default (admin activity only).
Reasonable for a club bot; if per-read auditing is ever wanted, enable Data Access logs
for Cloud Storage in IAM → Audit Logs (small log-volume cost).
