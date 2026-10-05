# Google Cloud Build Deployment

The Discord bot is a long-running gateway process hosted on a GCE `e2-micro` VM in
`us-central1-a`, managed by systemd and updated by Cloud Build. It is the only deployable.

## Pipeline

`discord-bot/cloudbuild.yaml` is a single-image pipeline:

1. Build the bot image from the repo (bot code, `content/`, and `summaries/` minus
   `missions/source-pdfs/`), tagged `$BUILD_ID` and `latest`.
2. Push both tags to Artifact Registry (`bfwg`, `us-central1` — colocated with the VM).
3. SSH to the VM: install `deploy/start-bot.sh` + the systemd unit, then restart the service
   with the immutable `$BUILD_ID` image. `start-bot.sh` fetches `discord-token` and `guild-id`
   from Secret Manager at service start and mounts `/var/lib/bfwg/state` for reminder dedup
   state.

## Triggering

The normal path is a push to `development`: `.github/workflows/discord-bot-tests.yml`
runs the bot tests, fast-forwards `main` to the tested commit, then
`.github/workflows/deploy.yml` runs from `workflow_run` and submits Cloud Build. Direct
pushes to `main` and manual **Deploy bot** runs also work. The deploy workflow
authenticates via Workload Identity Federation and runs `gcloud builds submit` against the
GitHub repository URL with `--git-source-revision` set to the tested commit SHA. That avoids
uploading the checked-out workspace to the legacy Cloud Build source staging bucket. There is
**no Cloud Build trigger object** to manage. Deploy by hand from a local checkout with
`gcloud builds submit --config discord-bot/cloudbuild.yaml .`.

Every tested `development` push deploys — docs-only changes included. That is intentional
(the VM always matches `main`); the workflow's `deploy-bot` concurrency group keeps a push
burst from stacking redundant deploys. The root `.gcloudignore` trims local manual source
uploads — it excludes `archive/` and the local-only `summaries/missions/source-pdfs/`.

See [`operations.md`](operations.md) for the WIF setup, the Artifact Registry cleanup policy,
IAM roles, rollback, and teardown of the parked analytics resources.
