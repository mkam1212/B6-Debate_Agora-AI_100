#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ALLOW_DEPLOY_ACTIONS=1 ./scripts/install_machine_dependencies.sh

Installs the system packages required to host one or more GizmoApp deployments
on a Debian/Ubuntu machine. Safe to re-run.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

REQUIRED_PACKAGES=(
  python3
  python3-venv
  python3-pip
  sqlite3
  git
  curl
  cron
)

missing_packages=()
for package in "${REQUIRED_PACKAGES[@]}"; do
  if ! dpkg -s "${package}" >/dev/null 2>&1; then
    missing_packages+=("${package}")
  fi
done

if (( ${#missing_packages[@]} == 0 )); then
  echo "All required machine packages are already installed."
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/require_explicit_approval.sh"
require_any_explicit_approval \
  "install host packages with apt and may require sudo" \
  ALLOW_DEPLOY_ACTIONS

if (( EUID == 0 )); then
  SUDO=()
elif command -v sudo >/dev/null 2>&1; then
  SUDO=(sudo)
else
  echo "Missing packages detected: ${missing_packages[*]}" >&2
  echo "Run this script as root or install sudo first." >&2
  exit 1
fi

echo "Installing missing machine packages: ${missing_packages[*]}"
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y "${missing_packages[@]}"

echo "Machine dependencies are installed."
