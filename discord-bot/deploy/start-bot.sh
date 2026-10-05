#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${CONFIG_DIR:-/etc/bfwg}"
RUN_DIR="${RUN_DIR:-/run/bfwg}"
STATE_DIR="${STATE_DIR:-/var/lib/bfwg/state}"
BOT_UID="${BOT_UID:-10001}"
BOT_GID="${BOT_GID:-10001}"
IMAGE_FILE="${CONFIG_DIR}/image-uri"
RUNTIME_CONFIG_FILE="${CONFIG_DIR}/runtime.env"
CONTAINER_ENV_FILE="${RUN_DIR}/bot.env"
SERVICE_NAME="${BOT_SERVICE_NAME:-bfwg-discord-bot}"
CONTAINER_NAME="${BOT_CONTAINER_NAME:-bfwg-discord-bot}"

shell_quote() {
  local value="${1:-}"
  printf "'%s'" "${value//\'/\'\\\'\'}"
}

metadata_value() {
  local path="$1"
  curl -fsS \
    -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/${path}"
}

project_id() {
  if [[ -n "${BFWG_PROJECT_ID:-}" ]]; then
    printf "%s" "${BFWG_PROJECT_ID}"
    return
  fi

  metadata_value "project/project-id"
}

require_command() {
  local name="$1"

  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "Required command not found: ${name}" >&2
    exit 1
  fi
}

secret_value() {
  local project="$1"
  local secret_name="$2"

  gcloud secrets versions access latest \
    --project="${project}" \
    --secret="${secret_name}"
}

write_runtime_config() {
  local image_uri="$1"
  local project="$2"
  local tmp_file
  tmp_file="$(mktemp "${CONFIG_DIR}/runtime.env.XXXXXX")"

  umask 077
  {
    printf "BFWG_PROJECT_ID=%s\n" "$(shell_quote "${project}")"
    printf "BOT_CONTAINER_NAME=%s\n" "$(shell_quote "${CONTAINER_NAME}")"
    printf "DISCORD_TOKEN_SECRET=%s\n" "$(shell_quote "${DISCORD_TOKEN_SECRET:-discord-token}")"
    printf "DISCORD_GUILD_ID_SECRET=%s\n" "$(shell_quote "${DISCORD_GUILD_ID_SECRET:-guild-id}")"
    printf "SUMMARIES_DIR=%s\n" "$(shell_quote "${SUMMARIES_DIR:-/app/summaries}")"
    printf "DISCORD_SYNC_COMMANDS_ON_STARTUP=%s\n" "$(shell_quote "${DISCORD_SYNC_COMMANDS_ON_STARTUP:-false}")"
    printf "CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED=%s\n" "$(shell_quote "${CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED:-false}")"
    printf "CLUB_NIGHT_REMINDER_CHANNEL_ID=%s\n" "$(shell_quote "${CLUB_NIGHT_REMINDER_CHANNEL_ID:-}")"
    printf "CLUB_NIGHT_REMINDER_DAYS_BEFORE=%s\n" "$(shell_quote "${CLUB_NIGHT_REMINDER_DAYS_BEFORE:-3}")"
    printf "CLUB_NIGHT_REMINDER_TIME=%s\n" "$(shell_quote "${CLUB_NIGHT_REMINDER_TIME:-19:00}")"
    printf "CLUB_NIGHT_REMINDER_TIMEZONE=%s\n" "$(shell_quote "${CLUB_NIGHT_REMINDER_TIMEZONE:-Europe/London}")"
    printf "CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS=%s\n" "$(shell_quote "${CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS:-3600}")"
    printf "CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED=%s\n" "$(shell_quote "${CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED:-false}")"
    printf "CLUB_NIGHT_ANNOUNCEMENT_TIME=%s\n" "$(shell_quote "${CLUB_NIGHT_ANNOUNCEMENT_TIME:-13:00}")"
    printf "CLUB_NIGHT_SIGNUP_STATE_PATH=%s\n" "$(shell_quote "${CLUB_NIGHT_SIGNUP_STATE_PATH:-/app/state/club_night_signups.json}")"
    printf "CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH=%s\n" "$(shell_quote "${CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH:-/app/state/club_night_pairing_history.json}")"
    printf "CLUB_NIGHT_PAIRING_LOOKBACK=%s\n" "$(shell_quote "${CLUB_NIGHT_PAIRING_LOOKBACK:-2}")"
    printf "CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH=%s\n" "$(shell_quote "${CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH:-/app/state/club_night_pairing_constraints.json}")"
    printf "FEEDBACK_STATE_PATH=%s\n" "$(shell_quote "${FEEDBACK_STATE_PATH:-/app/state/feedback.json}")"
    printf "CLUB_NIGHT_GAMES_STATE_PATH=%s\n" "$(shell_quote "${CLUB_NIGHT_GAMES_STATE_PATH:-/app/state/club_night_games.json}")"
    printf "COMMAND_DOCS_CHANNEL_ID=%s\n" "$(shell_quote "${COMMAND_DOCS_CHANNEL_ID:-}")"
    printf "GAME_ORGANISATION_EXPORT_ENABLED=%s\n" "$(shell_quote "${GAME_ORGANISATION_EXPORT_ENABLED:-false}")"
    printf "GAME_ORGANISATION_CHANNEL_ID=%s\n" "$(shell_quote "${GAME_ORGANISATION_CHANNEL_ID:-}")"
    printf "GAME_ORGANISATION_EXPORT_PATH=%s\n" "$(shell_quote "${GAME_ORGANISATION_EXPORT_PATH:-/app/state/game_organisation_export.json}")"
    printf "GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS=%s\n" "$(shell_quote "${GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS:-24}")"
    printf "GCS_STATE_UPLOAD_ENABLED=%s\n" "$(shell_quote "${GCS_STATE_UPLOAD_ENABLED:-false}")"
    printf "GCS_STATE_BUCKET=%s\n" "$(shell_quote "${GCS_STATE_BUCKET:-}")"
  } > "${tmp_file}"

  install -m 0600 "${tmp_file}" "${RUNTIME_CONFIG_FILE}"
  rm -f "${tmp_file}"
  printf "%s\n" "${image_uri}" > "${IMAGE_FILE}"
  chmod 0600 "${IMAGE_FILE}"
}

write_container_env() {
  local project="$1"
  local token_secret="${DISCORD_TOKEN_SECRET:-discord-token}"
  local guild_secret="${DISCORD_GUILD_ID_SECRET:-guild-id}"
  local tmp_file
  tmp_file="$(mktemp "${RUN_DIR}/bot.env.XXXXXX")"

  umask 077
  {
    printf "DISCORD_TOKEN=%s\n" "$(secret_value "${project}" "${token_secret}")"
    printf "DISCORD_GUILD_ID=%s\n" "$(secret_value "${project}" "${guild_secret}")"
    printf "SUMMARIES_DIR=%s\n" "${SUMMARIES_DIR:-/app/summaries}"
    printf "DISCORD_SYNC_COMMANDS_ON_STARTUP=%s\n" "${DISCORD_SYNC_COMMANDS_ON_STARTUP:-false}"
    printf "CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED=%s\n" "${CLUB_NIGHT_REMINDER_SCHEDULER_ENABLED:-false}"
    printf "CLUB_NIGHT_REMINDER_CHANNEL_ID=%s\n" "${CLUB_NIGHT_REMINDER_CHANNEL_ID:-}"
    printf "CLUB_NIGHT_REMINDER_DAYS_BEFORE=%s\n" "${CLUB_NIGHT_REMINDER_DAYS_BEFORE:-3}"
    printf "CLUB_NIGHT_REMINDER_TIME=%s\n" "${CLUB_NIGHT_REMINDER_TIME:-19:00}"
    printf "CLUB_NIGHT_REMINDER_TIMEZONE=%s\n" "${CLUB_NIGHT_REMINDER_TIMEZONE:-Europe/London}"
    printf "CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS=%s\n" "${CLUB_NIGHT_REMINDER_CHECK_INTERVAL_SECONDS:-3600}"
    printf "CLUB_NIGHT_REMINDER_STATE_PATH=%s\n" "${CLUB_NIGHT_REMINDER_STATE_PATH:-/app/state/scheduled_posts.json}"
    printf "CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED=%s\n" "${CLUB_NIGHT_ANNOUNCEMENT_SCHEDULER_ENABLED:-false}"
    printf "CLUB_NIGHT_ANNOUNCEMENT_TIME=%s\n" "${CLUB_NIGHT_ANNOUNCEMENT_TIME:-13:00}"
    printf "CLUB_NIGHT_SIGNUP_STATE_PATH=%s\n" "${CLUB_NIGHT_SIGNUP_STATE_PATH:-/app/state/club_night_signups.json}"
    printf "CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH=%s\n" "${CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH:-/app/state/club_night_pairing_history.json}"
    printf "CLUB_NIGHT_PAIRING_LOOKBACK=%s\n" "${CLUB_NIGHT_PAIRING_LOOKBACK:-2}"
    printf "CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH=%s\n" "${CLUB_NIGHT_PAIRING_CONSTRAINTS_STATE_PATH:-/app/state/club_night_pairing_constraints.json}"
    printf "FEEDBACK_STATE_PATH=%s\n" "${FEEDBACK_STATE_PATH:-/app/state/feedback.json}"
    printf "CLUB_NIGHT_GAMES_STATE_PATH=%s\n" "${CLUB_NIGHT_GAMES_STATE_PATH:-/app/state/club_night_games.json}"
    printf "COMMAND_DOCS_CHANNEL_ID=%s\n" "${COMMAND_DOCS_CHANNEL_ID:-}"
    printf "GAME_ORGANISATION_EXPORT_ENABLED=%s\n" "${GAME_ORGANISATION_EXPORT_ENABLED:-false}"
    printf "GAME_ORGANISATION_CHANNEL_ID=%s\n" "${GAME_ORGANISATION_CHANNEL_ID:-}"
    printf "GAME_ORGANISATION_EXPORT_PATH=%s\n" "${GAME_ORGANISATION_EXPORT_PATH:-/app/state/game_organisation_export.json}"
    printf "GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS=%s\n" "${GAME_ORGANISATION_EXPORT_MAX_AGE_HOURS:-24}"
    printf "GCS_STATE_UPLOAD_ENABLED=%s\n" "${GCS_STATE_UPLOAD_ENABLED:-false}"
    printf "GCS_STATE_BUCKET=%s\n" "${GCS_STATE_BUCKET:-}"

    if [[ -n "${BOT_OWNER_USER_IDS:-}" ]]; then
      printf "BOT_OWNER_USER_IDS=%s\n" "${BOT_OWNER_USER_IDS}"
    fi
  } > "${tmp_file}"

  install -m 0600 "${tmp_file}" "${CONTAINER_ENV_FILE}"
  rm -f "${tmp_file}"
}

deploy_image() {
  local image_uri="$1"
  local project
  project="$(project_id)"

  install -d -m 0755 "${CONFIG_DIR}"
  write_runtime_config "${image_uri}" "${project}"
  systemctl restart "${SERVICE_NAME}"
}

run_container() {
  require_command curl
  require_command docker
  require_command gcloud

  if [[ -f "${RUNTIME_CONFIG_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "${RUNTIME_CONFIG_FILE}"
    set +a
  fi

  if [[ ! -s "${IMAGE_FILE}" ]]; then
    echo "Missing image URI file: ${IMAGE_FILE}" >&2
    exit 1
  fi

  local image_uri
  local project
  image_uri="$(<"${IMAGE_FILE}")"
  project="$(project_id)"

  install -d -m 0755 "${RUN_DIR}"
  # Keep the host bind mount writable by the unprivileged container user. The
  # numeric IDs are defined in the Dockerfile and can be overridden for custom images.
  install -d -m 0750 -o "${BOT_UID}" -g "${BOT_GID}" "${STATE_DIR}"
  write_container_env "${project}"

  # Register the Artifact Registry credential helper for whatever registry the image
  # lives in, so `docker pull` authenticates via the VM service account. Region-agnostic:
  # derives the host from the image URI, so changing _REGION needs no manual VM step.
  local registry_host="${image_uri%%/*}"
  gcloud auth configure-docker "${registry_host}" --quiet

  docker pull "${image_uri}"
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

  exec docker run --rm \
    --name "${CONTAINER_NAME}" \
    --env-file "${CONTAINER_ENV_FILE}" \
    --volume "${STATE_DIR}:/app/state" \
    --log-driver gcplogs \
    --log-opt gcp-log-cmd=true \
    "${image_uri}"
}

case "$#" in
  0)
    run_container
    ;;
  1)
    deploy_image "$1"
    ;;
  *)
    echo "Usage: $0 [image-uri]" >&2
    exit 2
    ;;
esac
