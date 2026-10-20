#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${ROOT_DIR}/var/log/deploy-cron.log"
MAX_BYTES=$((5 * 1024 * 1024))
mkdir -p "${ROOT_DIR}/var/log"

if [[ -f "${LOG_FILE}" ]] && (( $(stat -c %s "${LOG_FILE}") >= MAX_BYTES )); then
  for suffix in 3 2 1; do
    if [[ -f "${LOG_FILE}.${suffix}" ]]; then
      next_suffix=$((suffix + 1))
      mv "${LOG_FILE}.${suffix}" "${LOG_FILE}.${next_suffix}"
    fi
  done
  mv "${LOG_FILE}" "${LOG_FILE}.1"
  rm -f "${LOG_FILE}.4"
fi

cd "${ROOT_DIR}"
"${ROOT_DIR}/scripts/deploy_from_git.sh" >>"${LOG_FILE}" 2>&1
