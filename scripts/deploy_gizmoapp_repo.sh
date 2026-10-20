#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ALLOW_DEPLOY_ACTIONS=1 deploy_gizmoapp_repo.sh REPO_URL [options]

Deploy a GizmoApp-derived repository by repo URL alone.
The app name, checkout directory, and URL prefix are inferred from the repo name.

Examples:
  ALLOW_DEPLOY_ACTIONS=1 deploy_gizmoapp_repo.sh git@github.com:kevinlb1/GizmoAppKLB1.git
  ALLOW_DEPLOY_ACTIONS=1 deploy_gizmoapp_repo.sh https://github.com/kevinlb1/GizmoAppKLB1.git --shell text

Defaults:
  controller dir: /home/kevinlb/bin/GizmoApp
  install dir:    /home/kevinlb/bin/<repo-name>
  public URL:     http://vickrey10.cs.ubc.ca/<repo-name>/
  branch:         main
  shell:          graphical

Options:
  --branch BRANCH         branch to deploy (default: main)
  --shell SHELL           graphical or text (default: graphical)
  --controller-dir DIR    checkout that contains install_deployment_instance.sh
                          (default: /home/kevinlb/bin/GizmoApp)
  --base-dir DIR          parent directory for app checkouts
                          (default: /home/kevinlb/bin)
  --domain DOMAIN         public hostname (default: vickrey10.cs.ubc.ca)
  --port PORT             fixed local gunicorn port
  --app-title TITLE       app title override; default is repo name
  -h, --help              show this help
EOF
}

infer_repo_name() {
  local repo_url="$1"
  local trimmed="${repo_url%/}"
  local basename_part="${trimmed##*/}"
  basename_part="${basename_part%.git}"
  if [[ -z "${basename_part}" ]]; then
    echo "Could not infer repository name from ${repo_url}" >&2
    exit 1
  fi
  printf '%s\n' "${basename_part}"
}

REPO_URL="${1:-}"
if [[ -z "${REPO_URL}" || "${REPO_URL}" == "-h" || "${REPO_URL}" == "--help" ]]; then
  usage
  exit 0
fi
shift

BRANCH="main"
SHELL_VARIANT="graphical"
CONTROLLER_DIR="${GIZMOAPP_CONTROLLER_DIR:-/home/kevinlb/bin/GizmoApp}"
BASE_DIR="/home/kevinlb/bin"
DOMAIN="vickrey10.cs.ubc.ca"
PORT=""
APP_TITLE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)
      BRANCH="$2"
      shift 2
      ;;
    --shell)
      SHELL_VARIANT="$2"
      shift 2
      ;;
    --controller-dir)
      CONTROLLER_DIR="$2"
      shift 2
      ;;
    --base-dir)
      BASE_DIR="$2"
      shift 2
      ;;
    --domain)
      DOMAIN="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --app-title)
      APP_TITLE="$2"
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/require_explicit_approval.sh"
require_any_explicit_approval \
  "deploy a repository by invoking the instance installer" \
  ALLOW_DEPLOY_ACTIONS

INSTALL_SCRIPT="${CONTROLLER_DIR}/scripts/install_deployment_instance.sh"
if [[ ! -x "${INSTALL_SCRIPT}" ]]; then
  echo "Could not find installer at ${INSTALL_SCRIPT}" >&2
  echo "Set --controller-dir or GIZMOAPP_CONTROLLER_DIR to the GizmoApp controller checkout." >&2
  exit 1
fi

NAME="$(infer_repo_name "${REPO_URL}")"
if [[ -z "${APP_TITLE}" ]]; then
  APP_TITLE="${NAME}"
fi

args=(
  --name "${NAME}"
  --repo-url "${REPO_URL}"
  --branch "${BRANCH}"
  --shell "${SHELL_VARIANT}"
  --app-title "${APP_TITLE}"
  --base-dir "${BASE_DIR}"
  --domain "${DOMAIN}"
)

if [[ -n "${PORT}" ]]; then
  args+=(--port "${PORT}")
fi

echo "Deploying ${REPO_URL}"
echo "  name: ${NAME}"
echo "  checkout: ${BASE_DIR}/${NAME}"
echo "  url: http://${DOMAIN}/${NAME}/"
echo "  shell: ${SHELL_VARIANT}"
echo

"${INSTALL_SCRIPT}" "${args[@]}"
