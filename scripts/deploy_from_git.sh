#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_HELPER="${ROOT_DIR}/scripts/envfile.py"
SYNC_DEPLOY_ENV="${ROOT_DIR}/scripts/sync_deploy_env.sh"
DEPLOY_MARKER="${ROOT_DIR}/var/run/deployed-commit"
DEPLOY_LOCK="${ROOT_DIR}/var/run/deploy.lock"
ENV_BACKUP="${ROOT_DIR}/var/run/.env.before-deploy"
source "${ROOT_DIR}/scripts/require_explicit_approval.sh"
cd "${ROOT_DIR}"

require_any_explicit_approval \
  "fetch from Git, validate and merge remote changes, install changed dependencies, migrate the database, and reload services" \
  ALLOW_DEPLOY_ACTIONS

ensure_user_systemd_env() {
  if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  fi
  if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "${XDG_RUNTIME_DIR}/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
  fi
}

restart_service() {
  if [[ -n "${GIZMOAPP_SYSTEMD_USER_SERVICE:-}" ]] && command -v systemctl >/dev/null 2>&1; then
    ensure_user_systemd_env
    systemctl --user restart "${GIZMOAPP_SYSTEMD_USER_SERVICE}"
    return
  fi
  if [[ -n "${GIZMOAPP_GUNICORN_PID_FILE:-}" ]] && [[ -f "${GIZMOAPP_GUNICORN_PID_FILE}" ]]; then
    kill -HUP "$(cat "${GIZMOAPP_GUNICORN_PID_FILE}")"
    return
  fi
  return 1
}

reload_service() {
  if [[ -n "${GIZMOAPP_SYSTEMD_USER_SERVICE:-}" ]] && command -v systemctl >/dev/null 2>&1; then
    ensure_user_systemd_env
    systemctl --user reload "${GIZMOAPP_SYSTEMD_USER_SERVICE}" || \
      systemctl --user restart "${GIZMOAPP_SYSTEMD_USER_SERVICE}"
    return
  fi
  if [[ -n "${GIZMOAPP_GUNICORN_PID_FILE:-}" ]] && [[ -f "${GIZMOAPP_GUNICORN_PID_FILE}" ]]; then
    kill -HUP "$(cat "${GIZMOAPP_GUNICORN_PID_FILE}")"
    return
  fi
  return 1
}

mkdir -p "${ROOT_DIR}/var/log" "${ROOT_DIR}/var/data" "${ROOT_DIR}/var/run" "${ROOT_DIR}/var/backups"
if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required for safe cron deployments." >&2
  exit 1
fi
exec 9>"${DEPLOY_LOCK}"
if ! flock -n 9; then
  echo "Another deployment is already running; skipping this cron invocation."
  exit 0
fi

if [[ -f "${ROOT_DIR}/.env" ]]; then
  while IFS= read -r -d '' env_entry; do
    export "${env_entry}"
  done < <(python3 "${ENV_HELPER}" load "${ROOT_DIR}/.env")
fi

BRANCH="${GIZMOAPP_DEPLOY_BRANCH:-main}"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
PIP_BIN="${ROOT_DIR}/.venv/bin/pip"
START_HEAD="$(git rev-parse HEAD)"
ROLLBACK_HEAD="${START_HEAD}"
DEPLOY_STARTED=0

rollback_deploy() {
  local status="$?"
  trap - ERR INT TERM
  if (( DEPLOY_STARTED != 0 )); then
    echo "Deployment failed; restoring tracked code to ${ROLLBACK_HEAD}." >&2
    git reset --hard "${ROLLBACK_HEAD}" >/dev/null
    if [[ -f "${ENV_BACKUP}" ]]; then
      cp "${ENV_BACKUP}" "${ROOT_DIR}/.env"
      chmod 600 "${ROOT_DIR}/.env"
    fi
    if [[ -x "${SYNC_DEPLOY_ENV}" ]]; then
      if "${SYNC_DEPLOY_ENV}"; then
        :
      else
        sync_status="$?"
        if [[ "${sync_status}" -ne 10 ]]; then
          echo "Could not restore tracked deployment settings in .env." >&2
        fi
      fi
    fi
    if ! restart_service; then
      echo "Could not restart the previous release automatically; inspect the service immediately." >&2
    fi
    echo "The database backup in var/backups was retained for recovery if needed." >&2
  fi
  rm -f "${ENV_BACKUP}"
  exit "${status}"
}
trap rollback_deploy ERR INT TERM

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Tracked files are dirty. Refusing to deploy over local changes." >&2
  exit 1
fi
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Virtualenv is missing. Run ./scripts/install_checkout.sh or ./scripts/install_server.sh first." >&2
  exit 1
fi

git fetch origin "${BRANCH}"
read -r LOCAL_AHEAD REMOTE_AHEAD <<<"$(git rev-list --left-right --count HEAD...origin/${BRANCH})"
if (( LOCAL_AHEAD > 0 && REMOTE_AHEAD > 0 )); then
  echo "Local branch has diverged from origin/${BRANCH}. Manual intervention required." >&2
  exit 1
fi
if (( LOCAL_AHEAD > 0 )); then
  echo "Local checkout is ahead of origin/${BRANCH}. Refusing to overwrite local commits." >&2
  exit 1
fi

DEPLOYED_HEAD=""
if [[ -f "${DEPLOY_MARKER}" ]]; then
  DEPLOYED_HEAD="$(tr -d '[:space:]' < "${DEPLOY_MARKER}")"
fi
if (( REMOTE_AHEAD == 0 )) && [[ "${DEPLOYED_HEAD}" == "${START_HEAD}" ]]; then
  echo "Already up to date."
  exit 0
fi
if (( REMOTE_AHEAD == 0 )) && [[ -n "${DEPLOYED_HEAD}" ]] && git cat-file -e "${DEPLOYED_HEAD}^{commit}" 2>/dev/null; then
  ROLLBACK_HEAD="${DEPLOYED_HEAD}"
fi

DEPLOY_STARTED=1
if [[ -f "${ROOT_DIR}/.env" ]]; then
  cp "${ROOT_DIR}/.env" "${ENV_BACKUP}"
fi

if (( REMOTE_AHEAD != 0 )); then
  git merge --ff-only "origin/${BRANCH}"
fi
CHANGED_FILES="$(git diff --name-only "${ROLLBACK_HEAD}" HEAD)"
env_changed=0
if [[ -x "${SYNC_DEPLOY_ENV}" ]]; then
  if "${SYNC_DEPLOY_ENV}"; then
    :
  else
    sync_status="$?"
    if [[ "${sync_status}" -eq 10 ]]; then
      env_changed=1
    else
      exit "${sync_status}"
    fi
  fi
fi

if grep -Eq '^server/requirements[^/]*\.txt$' <<<"${CHANGED_FILES}"; then
  "${PIP_BIN}" install --upgrade pip wheel
  "${PIP_BIN}" install --requirement "${ROOT_DIR}/server/requirements.txt"
fi

"${ROOT_DIR}/scripts/run_local_validation.sh"

if [[ -f "${GIZMOAPP_DB_PATH:-${ROOT_DIR}/var/data/gizmoapp.sqlite3}" ]]; then
  BACKUP_PATH="${ROOT_DIR}/var/backups/gizmoapp-$(date -u +%Y%m%dT%H%M%SZ)-${ROLLBACK_HEAD:0:12}.sqlite3"
  "${PYTHON_BIN}" "${ROOT_DIR}/server/manage.py" backup-db --output "${BACKUP_PATH}"
  mapfile -t OLD_BACKUPS < <(find "${ROOT_DIR}/var/backups" -maxdepth 1 -type f -name 'gizmoapp-*.sqlite3' -printf '%T@ %p\n' | sort -rn | tail -n +6 | cut -d' ' -f2-)
  if (( ${#OLD_BACKUPS[@]} > 0 )); then
    rm -f -- "${OLD_BACKUPS[@]}"
  fi
fi
"${PYTHON_BIN}" "${ROOT_DIR}/server/manage.py" init-db

needs_reload=0
while IFS= read -r path; do
  [[ -z "${path}" ]] && continue
  case "${path}" in
    AGENTS.md|README.md|.gitignore|.env.example|deploy/user-crontab.example|deploy/nginx-location.example.conf|server/gizmoapp_server/static/*)
      ;;
    *)
      needs_reload=1
      break
      ;;
  esac
done <<<"${CHANGED_FILES}"

if (( env_changed != 0 )); then
  restart_service
elif (( needs_reload != 0 )); then
  reload_service
fi

READY_URL="http://127.0.0.1:${GIZMOAPP_PORT:-8001}${GIZMOAPP_URL_PREFIX:-}/readyz"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_runtime_ready.py" --url "${READY_URL}" --timeout 25

NEW_HEAD="$(git rev-parse HEAD)"
printf '%s\n' "${NEW_HEAD}" > "${DEPLOY_MARKER}.tmp"
mv "${DEPLOY_MARKER}.tmp" "${DEPLOY_MARKER}"
rm -f "${ENV_BACKUP}"
DEPLOY_STARTED=0
trap - ERR INT TERM
echo "Deployment completed successfully at ${NEW_HEAD}."
