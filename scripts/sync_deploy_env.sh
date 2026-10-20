#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_HELPER="${ROOT_DIR}/scripts/envfile.py"
TRACKED_ENV="${ROOT_DIR}/deploy/app.env"
LIVE_ENV="${ROOT_DIR}/.env"

ALLOWED_KEYS=(
  GIZMOAPP_SHELL
)

is_allowed_key() {
  local candidate="$1"
  local key
  for key in "${ALLOWED_KEYS[@]}"; do
    if [[ "${candidate}" == "${key}" ]]; then
      return 0
    fi
  done
  return 1
}

if [[ ! -f "${TRACKED_ENV}" || ! -f "${LIVE_ENV}" ]]; then
  exit 0
fi

changed=0

while IFS= read -r -d '' env_entry; do
  key="${env_entry%%=*}"
  value="${env_entry#*=}"

  if ! is_allowed_key "${key}"; then
    echo "Ignoring unmanaged deploy setting ${key} from ${TRACKED_ENV}."
    continue
  fi

  current_value="$(python3 "${ENV_HELPER}" get "${LIVE_ENV}" "${key}")"
  if [[ "${current_value}" != "${value}" ]]; then
    python3 "${ENV_HELPER}" set "${LIVE_ENV}" "${key}" "${value}"
    echo "Updated ${key} in ${LIVE_ENV} from ${TRACKED_ENV}."
    changed=1
  fi
done < <(python3 "${ENV_HELPER}" load "${TRACKED_ENV}")

chmod 600 "${LIVE_ENV}"

if (( changed != 0 )); then
  exit 10
fi

exit 0
