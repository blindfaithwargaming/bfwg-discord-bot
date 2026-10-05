# Club Night Scheduler Deployment Test

Use this checklist after changing or verifying the club-night schedulers (reminder and
announcement). The production deploy config enables both; local/default app config
remains off unless `CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED=true` /
`CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED=true` are set. Duplicate protection is a
local JSON marker file on the VM — there is no Firestore dependency.

The reminder scheduler reads the next club-night date from existing Discord forum posts
in `CLUB_NIGHT_FORUM_CHANNEL_ID`, with titles such as `Organised Play - 11th June`. The
announcement scheduler creates that forum post automatically from
`CLUB_NIGHT_ANNOUNCEMENT_TIME` (default `13:00` local) on the day after the previous
club night, using the Battlefields ticketing page as the source of truth for the next
date — the same source `/post-club-night` uses.

---

## 1. Verify locally before deployment

From `discord-bot/`:

```bash
python -m compileall bot tests -q
python -m pytest --tb=no -q
```

Expected: all tests pass, zero skips.

---

## 2. Confirm scheduler deploy config

The reminder scheduler only runs when both are set; if enabled without a channel, the
bot fails fast at startup config validation:

```text
CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED=true
CLUB_NIGHT_REMINDER_CHANNEL_ID=REMINDER_CHANNEL_ID
```

The announcement scheduler needs only its enable flag (the forum channel has a
production default):

```text
CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED=true
```

Production `cloudbuild.yaml` sets these for the live reminder channel. For a one-off
same-day verification, deploy with temporary substitutions:

```bash
gcloud builds submit --config discord-bot/cloudbuild.yaml \
  --substitutions _CLUB_NIGHT_REMINDER_DAYS_BEFORE=0,_CLUB_NIGHT_REMINDER_TIME=15:00 \
  .
```

Optional knobs (defaults shown): `_CLUB_NIGHT_REMINDER_DAYS_BEFORE=3`,
`_CLUB_NIGHT_REMINDER_TIME=19:00`, `_CLUB_NIGHT_REMINDER_TIMEZONE=Europe/London`,
`_CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS=3600`,
`_CLUB_NIGHT_ANNOUNCEMENT_TIME=13:00` (the announcement scheduler shares the reminder
timezone and check interval).

---

## 3. Confirm the scheduler started

On the VM:

```bash
sudo journalctl -u bfwg-discord-bot -f
```

Expected log line: `Started club night scheduler interval=3600s reminder=True announcement=True.`
Each tick logs `Club night reminder scheduler tick status=… reason=… post_id=…` and
`Club night announcement scheduler tick status=… reason=… post_id=…`.

Common reminder `status/reason` values: `skipped/not_due` (most ticks),
`skipped/channel_unconfigured` (channel not set), `skipped/club_night_forum_post_not_found`
(no upcoming matching forum post), `posted/posted` (reminder sent),
`skipped/already_posted` (dedup hit).

Common announcement `status/reason` values: `skipped/announcement_exists` (most ticks —
a current or future forum post already announced), `skipped/not_due` (day after the
previous club night but before the post time), `failed/next_club_night_unavailable`
(due, but Battlefields does not list the next event yet — retried each tick),
`posted/posted` (forum post created), `skipped/already_posted` (dedup hit).

---

## 4. Confirm the local marker file

Both schedulers write dedup markers to `CLUB_NIGHT_REMINDER_STATE_PATH`
(`/app/state/scheduled_posts.json`), mounted from `/var/lib/bfwg/state` on the VM so it
survives container restarts:

```bash
sudo cat /var/lib/bfwg/state/scheduled_posts.json
```

After a reminder posts you should see a marker keyed by guild → `club-night-reminder-<date>`
with `"status": "posted"` and the `message_id`; after an automatic announcement, a
`club-night-announcement-<date>` marker whose `message_id` is the created thread ID.

---

## 5. Confirm no duplicate reminders

- A second tick for the same club-night date logs `skipped/already_posted` and sends nothing.
- Restart the service (`sudo systemctl restart bfwg-discord-bot`); because the marker file
  persists on the host volume, the next tick still sees `already_posted`.

To force re-evaluation (e.g. during testing), remove the marker file:

```bash
sudo rm /var/lib/bfwg/state/scheduled_posts.json
```
