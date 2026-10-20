#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ALLOW_DEPLOY_ACTIONS=1 ./scripts/register_nginx_instance_snippet.sh --name NAME --snippet PATH [options]

Copies one generated nginx location snippet into the managed include directory,
then validates and reloads nginx. Use this after the one-time router bootstrap
for app instances that were installed before automatic registration existed.

Options:
  --name NAME          instance name, used for the target file NAME.conf
  --snippet PATH       source snippet path, usually <checkout>/var/generated/nginx-location.conf
  --managed-dir DIR    managed snippet dir (default: /etc/nginx/gizmoapp-instances)
  -h, --help           Show this help
EOF
}

NAME=""
SNIPPET=""
MANAGED_DIR="/etc/nginx/gizmoapp-instances"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      NAME="$2"
      shift 2
      ;;
    --snippet)
      SNIPPET="$2"
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

if [[ -z "${NAME}" || -z "${SNIPPET}" ]]; then
  usage
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/require_explicit_approval.sh"
require_any_explicit_approval \
  "write nginx config under /etc and reload nginx with sudo" \
  ALLOW_DEPLOY_ACTIONS

if [[ ! -f "${SNIPPET}" ]]; then
  echo "Could not find snippet file: ${SNIPPET}" >&2
  exit 1
fi

if ! command -v sudo >/dev/null 2>&1 || ! command -v nginx >/dev/null 2>&1; then
  echo "sudo and nginx are required." >&2
  exit 1
fi

target="${MANAGED_DIR}/${NAME}.conf"
sudo install -d -m 755 "${MANAGED_DIR}"
sudo install -m 644 "${SNIPPET}" "${target}"
sudo nginx -t
sudo systemctl reload nginx

echo "Registered nginx snippet:"
echo "  source: ${SNIPPET}"
echo "  target: ${target}"
