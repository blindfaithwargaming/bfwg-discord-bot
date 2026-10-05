from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent.parent



def test_cloudbuild_forwards_signup_state_path_to_vm() -> None:
    cloudbuild = (REPO_ROOT / "discord-bot" / "cloudbuild.yaml").read_text(
        encoding="utf-8"
    )
    deploy_step = cloudbuild.split("sudo env BFWG_PROJECT_ID", maxsplit=1)[1]

    assert (
        "_CLUB_NIGHT_SIGNUP_STATE_PATH: /app/state/club_night_signups.json"
        in cloudbuild
    )
    assert (
        "CLUB_NIGHT_SIGNUP_STATE_PATH=${_CLUB_NIGHT_SIGNUP_STATE_PATH}"
        in deploy_step
    )


def test_start_bot_forwards_signup_state_path_to_container() -> None:
    script = (REPO_ROOT / "discord-bot" / "deploy" / "start-bot.sh").read_text(
        encoding="utf-8"
    )

    assert (
        'printf "CLUB_NIGHT_SIGNUP_STATE_PATH=%s\\n" '
        '"$(shell_quote "${CLUB_NIGHT_SIGNUP_STATE_PATH:-/app/state/club_night_signups.json}")"'
        in script
    )
    assert (
        'printf "CLUB_NIGHT_SIGNUP_STATE_PATH=%s\\n" '
        '"${CLUB_NIGHT_SIGNUP_STATE_PATH:-/app/state/club_night_signups.json}"'
        in script
    )


def test_cloudbuild_forwards_pairing_history_config_to_vm() -> None:
    cloudbuild = (REPO_ROOT / "discord-bot" / "cloudbuild.yaml").read_text(
        encoding="utf-8"
    )
    deploy_step = cloudbuild.split("sudo env BFWG_PROJECT_ID", maxsplit=1)[1]

    assert (
        "_CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH: "
        "/app/state/club_night_pairing_history.json"
        in cloudbuild
    )
    assert '_CLUB_NIGHT_PAIRING_LOOKBACK: "2"' in cloudbuild
    assert (
        "CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH="
        "${_CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH}"
        in deploy_step
    )
    assert (
        "CLUB_NIGHT_PAIRING_LOOKBACK=${_CLUB_NIGHT_PAIRING_LOOKBACK}"
        in deploy_step
    )


def test_start_bot_forwards_pairing_history_config_to_container() -> None:
    script = (REPO_ROOT / "discord-bot" / "deploy" / "start-bot.sh").read_text(
        encoding="utf-8"
    )

    assert (
        'printf "CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH=%s\\n" '
        '"$(shell_quote "${CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH:'
        '-/app/state/club_night_pairing_history.json}")"'
        in script
    )
    assert (
        'printf "CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH=%s\\n" '
        '"${CLUB_NIGHT_PAIRING_HISTORY_STATE_PATH:'
        '-/app/state/club_night_pairing_history.json}"'
        in script
    )
    assert (
        'printf "CLUB_NIGHT_PAIRING_LOOKBACK=%s\\n" '
        '"$(shell_quote "${CLUB_NIGHT_PAIRING_LOOKBACK:-2}")"'
        in script
    )
    assert (
        'printf "CLUB_NIGHT_PAIRING_LOOKBACK=%s\\n" '
        '"${CLUB_NIGHT_PAIRING_LOOKBACK:-2}"'
        in script
    )


def test_cloudbuild_forwards_games_state_path_to_vm() -> None:
    cloudbuild = (REPO_ROOT / "discord-bot" / "cloudbuild.yaml").read_text(
        encoding="utf-8"
    )
    deploy_step = cloudbuild.split("sudo env BFWG_PROJECT_ID", maxsplit=1)[1]

    assert (
        "_CLUB_NIGHT_GAMES_STATE_PATH: /app/state/club_night_games.json"
        in cloudbuild
    )
    assert (
        "CLUB_NIGHT_GAMES_STATE_PATH=${_CLUB_NIGHT_GAMES_STATE_PATH}"
        in deploy_step
    )


def test_start_bot_forwards_games_state_path_to_container() -> None:
    script = (REPO_ROOT / "discord-bot" / "deploy" / "start-bot.sh").read_text(
        encoding="utf-8"
    )

    assert (
        'printf "CLUB_NIGHT_GAMES_STATE_PATH=%s\\n" '
        '"$(shell_quote "${CLUB_NIGHT_GAMES_STATE_PATH:-/app/state/club_night_games.json}")"'
        in script
    )
    assert (
        'printf "CLUB_NIGHT_GAMES_STATE_PATH=%s\\n" '
        '"${CLUB_NIGHT_GAMES_STATE_PATH:-/app/state/club_night_games.json}"'
        in script
    )
