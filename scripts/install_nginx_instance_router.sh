#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ALLOW_DEPLOY_ACTIONS=1 ./scripts/install_nginx_instance_router.sh --server-config PATH [options]

Bootstraps nginx once so future GizmoApp deployments can register themselves
with a single install command and no manual nginx file edits.

Options:
  --server-config PATH   nginx site file that contains the target server block
  --server-name NAME     server_name to match inside the config (recommended)
  --managed-dir DIR      directory for per-instance snippets
                         (default: /etc/nginx/gizmoapp-instances)
  -h, --help             Show this help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_HELPER="${SCRIPT_DIR}/nginx_router_bootstrap.py"
SERVER_CONFIG=""
SERVER_NAME=""
MANAGED_DIR="/etc/nginx/gizmoapp-instances"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-config)
      SERVER_CONFIG="$2"
      shift 2
      ;;
    --server-name)
      SERVER_NAME="$2"
      shift 2
      ;;
    --managed-dir)
      MANAGED_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${SERVER_CONFIG}" ]]; then
  usage
  exit 1
fi

source "${SCRIPT_DIR}/require_explicit_approval.sh"
require_any_explicit_approval \
  "write nginx config under /etc and reload nginx with sudo" \
  ALLOW_DEPLOY_ACTIONS

if [[ ! -f "${BOOTSTRAP_HELPER}" ]]; then
  echo "Missing ${BOOTSTRAP_HELPER}" >&2
  exit 1
fi

if ! command -v sudo >/dev/null 2>&1 || ! command -v nginx >/dev/null 2>&1; then
  echo "sudo and nginx are required." >&2
  exit 1
fi

if [[ ! -f "${SERVER_CONFIG}" ]]; then
  echo "Could not find nginx server config file: ${SERVER_CONFIG}" >&2
  exit 1
fi

tmp_config="$(mktemp)"
helper_args=("${BOOTSTRAP_HELPER}" "${SERVER_CONFIG}" "${MANAGED_DIR}/*.conf" --output "${tmp_config}")
if [[ -n "${SERVER_NAME}" ]]; then
  helper_args+=(--server-name "${SERVER_NAME}")
fi

python3 "${helper_args[@]}"
sudo install -d -m 755 "${MANAGED_DIR}"
sudo install -m 644 "${tmp_config}" "${SERVER_CONFIG}"
rm -f "${tmp_config}"

sudo nginx -t
sudo systemctl reload nginx

echo
echo "nginx instance router is ready."
echo "Managed snippet directory: ${MANAGED_DIR}"
echo "Updated server config: ${SERVER_CONFIG}"
echo
echo "Future deployments can now register nginx automatically from:"
echo "  ALLOW_DEPLOY_ACTIONS=1 ./scripts/install_deployment_instance.sh --name myapp --repo-url git@github.com:YOU/REPO.git"
