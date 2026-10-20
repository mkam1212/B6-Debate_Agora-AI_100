#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON:-python3}"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
PYDEPS_DIR="$ROOT_DIR/.pydeps"

"$PYTHON_BIN" scripts/check_js_syntax.py

run_unittests() {
  "$@" -m unittest discover -s tests -v
}

if [ -x "$VENV_PYTHON" ]; then
  if "$VENV_PYTHON" -c "import flask" >/dev/null 2>&1; then
    run_unittests "$VENV_PYTHON"
    exit 0
  fi
fi

if "$PYTHON_BIN" -c "import flask" >/dev/null 2>&1; then
  run_unittests "$PYTHON_BIN"
  exit 0
fi

if PYTHONPATH="$PYDEPS_DIR${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -c "import flask" >/dev/null 2>&1; then
  PYTHONPATH="$PYDEPS_DIR${PYTHONPATH:+:$PYTHONPATH}" run_unittests "$PYTHON_BIN"
  exit 0
fi

cat >&2 <<'EOF'
Validation requires Flask, but no already-installed local dependency set was found.
To keep validation escalation-free, this helper does not install packages.
Run ALLOW_NETWORK_INSTALL=1 make install from a user-approved shell, then retry make validate.
EOF
exit 2
