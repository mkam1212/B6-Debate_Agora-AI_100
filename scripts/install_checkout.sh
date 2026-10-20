#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ALLOW_NETWORK_INSTALL=1 ./scripts/install_checkout.sh

Initializes the current checkout for local serving on a machine that already has
system dependencies installed. Safe to re-run.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_HELPER="${ROOT_DIR}/scripts/envfile.py"
SYNC_DEPLOY_ENV="${ROOT_DIR}/scripts/sync_deploy_env.sh"
source "${ROOT_DIR}/scripts/require_explicit_approval.sh"
cd "${ROOT_DIR}"

# These values come from the platform after it verifies the image receipt.
# Keep project .env loading from replacing the selected dependency runtime.
preinstalled_dependencies="${CODINGWORKSPACE_PREINSTALLED_DEPENDENCIES:-0}"
dependency_python="${CODINGWORKSPACE_PREINSTALLED_PYTHON:-}"

require_any_explicit_approval \
  "create or update a virtualenv, install Python packages, and write local runtime files" \
  ALLOW_NETWORK_INSTALL ALLOW_DEPLOY_ACTIONS

if [[ ! -f "${ROOT_DIR}/.env.example" ]]; then
  echo "Missing .env.example in ${ROOT_DIR}" >&2
  exit 1
fi

if [[ ! -f "${ENV_HELPER}" ]]; then
  echo "Missing ${ENV_HELPER}" >&2
  exit 1
fi

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
  echo "Created ${ROOT_DIR}/.env from .env.example. Review the values before starting gunicorn."
fi

chmod 600 "${ROOT_DIR}/.env"

if [[ -x "${SYNC_DEPLOY_ENV}" ]]; then
  if "${SYNC_DEPLOY_ENV}"; then
    :
  else
    sync_status="$?"
    if [[ "${sync_status}" -eq 10 ]]; then
      echo "Applied git-tracked deployment settings from deploy/app.env."
    else
      exit "${sync_status}"
    fi
  fi
fi

while IFS= read -r -d '' env_entry; do
  export "${env_entry}"
done < <(python3 "${ENV_HELPER}" load "${ROOT_DIR}/.env")

mkdir -p "${ROOT_DIR}/var/data" "${ROOT_DIR}/var/log"

# The managed entry point is a tiny real directory and executable shim. An
# older CodingWorkspace image can remove and rebuild it even when that image
# does not contain the current immutable dependency runtime.
if [[ "${preinstalled_dependencies}" == "1" ]]; then
  if [[ ! -d "${ROOT_DIR}/.venv" || -L "${ROOT_DIR}/.venv" ||
        ! -d "${ROOT_DIR}/.venv/bin" || -L "${ROOT_DIR}/.venv/bin" ||
        ! -f "${ROOT_DIR}/.venv/bin/python" || -L "${ROOT_DIR}/.venv/bin/python" ||
        ! -x "${ROOT_DIR}/.venv/bin/python" ||
        "${dependency_python}" != /* || ! -f "${dependency_python}" ||
        -L "${dependency_python}" || ! -x "${dependency_python}" ||
        -w "${dependency_python}" ]]; then
    echo "Preinstalled dependencies require a managed Python shim and a read-only image Python." >&2
    exit 1
  fi
  echo "Using versioned image dependencies."
else
  dependency_python="${ROOT_DIR}/.venv/bin/python"
  if [[ ! -x "${dependency_python}" ]]; then
    python3 -m venv "${ROOT_DIR}/.venv"
  fi
  # Use the bundled pip without a separate installer-tools network upgrade.
  "${dependency_python}" -m pip install -r "${ROOT_DIR}/server/requirements.txt"
fi
"${dependency_python}" "${ROOT_DIR}/server/manage.py" init-db

describe_args=()
if [[ -n "${GIZMOAPP_SHELL:-}" ]]; then
  describe_args+=(--shell "${GIZMOAPP_SHELL}")
fi
"${dependency_python}" "${ROOT_DIR}/server/manage.py" describe "${describe_args[@]}"

echo
echo "Checkout install complete for ${ROOT_DIR}."
echo "Next steps:"
echo "  1. Review ${ROOT_DIR}/.env for machine-specific values only"
echo "  2. Start or reload the gunicorn user service for this checkout"
echo "  3. Ensure nginx routes traffic to the configured GIZMOAPP_URL_PREFIX and GIZMOAPP_PORT"
