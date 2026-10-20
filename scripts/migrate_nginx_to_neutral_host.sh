#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ALLOW_DEPLOY_ACTIONS=1 ./scripts/migrate_nginx_to_neutral_host.sh [options]

Copies a legacy app-named nginx site file to a neutral host file, enables the
new file, disables the old enabled path, bootstraps the managed GizmoApp nginx
include directory, validates nginx, and reloads it.

Defaults are tailored for the current vickrey10.cs.ubc.ca migration:
  legacy enabled path: /etc/nginx/sites-enabled/ai100
  new site name:       vickrey10
  managed include dir: /etc/nginx/gizmoapp-instances

Options:
  --legacy-enabled PATH  existing enabled nginx site path
  --new-site-name NAME   neutral site filename to create
  --managed-dir DIR      managed snippet dir (default: /etc/nginx/gizmoapp-instances)
  -h, --help             Show this help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_SCRIPT="${SCRIPT_DIR}/install_nginx_instance_router.sh"
LEGACY_ENABLED="/etc/nginx/sites-enabled/ai100"
NEW_SITE_NAME="vickrey10"
MANAGED_DIR="/etc/nginx/gizmoapp-instances"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --legacy-enabled)
      LEGACY_ENABLED="$2"
      shift 2
      ;;
    --new-site-name)
      NEW_SITE_NAME="$2"
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

if [[ ! -x "${BOOTSTRAP_SCRIPT}" ]]; then
  echo "Missing ${BOOTSTRAP_SCRIPT}" >&2
  exit 1
fi

source "${SCRIPT_DIR}/require_explicit_approval.sh"
require_any_explicit_approval \
  "copy and replace nginx config under /etc and reload nginx with sudo" \
  ALLOW_DEPLOY_ACTIONS

if ! command -v sudo >/dev/null 2>&1 || ! command -v nginx >/dev/null 2>&1; then
  echo "sudo and nginx are required." >&2
  exit 1
fi

if [[ ! -e "${LEGACY_ENABLED}" ]]; then
  echo "Could not find legacy enabled nginx site: ${LEGACY_ENABLED}" >&2
  exit 1
fi

legacy_real="$(readlink -f "${LEGACY_ENABLED}")"
legacy_name="$(basename "${LEGACY_ENABLED}")"
new_available="/etc/nginx/sites-available/${NEW_SITE_NAME}"
new_enabled="/etc/nginx/sites-enabled/${NEW_SITE_NAME}"
backup_dir="/etc/nginx/gizmoapp-migration-backups/$(date +%Y%m%d-%H%M%S)-${NEW_SITE_NAME}"

echo "Legacy enabled path: ${LEGACY_ENABLED}"
echo "Legacy source file: ${legacy_real}"
echo "New available file: ${new_available}"
echo "New enabled path: ${new_enabled}"
echo "Managed snippet dir: ${MANAGED_DIR}"

sudo install -d -m 755 "${backup_dir}" "$(dirname "${new_available}")" /etc/nginx/sites-enabled
sudo cp "${legacy_real}" "${backup_dir}/$(basename "${legacy_real}").bak"
if [[ "${legacy_real}" != "${LEGACY_ENABLED}" ]]; then
  printf '%s\n' "${LEGACY_ENABLED}" | sudo tee "${backup_dir}/legacy-enabled-path.txt" >/dev/null
fi

sudo cp "${legacy_real}" "${new_available}"
sudo ln -sfn "${new_available}" "${new_enabled}"
if [[ "${LEGACY_ENABLED}" != "${new_enabled}" ]]; then
  sudo rm -f "${LEGACY_ENABLED}"
fi

"${BOOTSTRAP_SCRIPT}" --server-config "${new_available}" --managed-dir "${MANAGED_DIR}"

echo
echo "Legacy nginx site migrated to a neutral host file."
echo "Backup directory: ${backup_dir}"
echo "Neutral host file: ${new_available}"
echo
echo "Next recommended step:"
echo "  Register any already-installed app snippets, for example:"
echo "  ALLOW_DEPLOY_ACTIONS=1 ./scripts/register_nginx_instance_snippet.sh --name gizmotest --snippet /home/kevinlb/bin/gizmotest/var/generated/nginx-location.conf"
