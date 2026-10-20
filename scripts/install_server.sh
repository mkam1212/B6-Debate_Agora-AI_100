#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/require_explicit_approval.sh"
require_any_explicit_approval \
  "install host packages, install Python packages, and initialize this checkout for serving" \
  ALLOW_DEPLOY_ACTIONS

"${ROOT_DIR}/scripts/install_machine_dependencies.sh"
"${ROOT_DIR}/scripts/install_checkout.sh"
