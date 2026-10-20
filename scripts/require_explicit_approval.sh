#!/usr/bin/env bash

require_any_explicit_approval() {
  local description="$1"
  shift

  local flag
  for flag in "$@"; do
    if [[ "${!flag:-0}" == "1" ]]; then
      return 0
    fi
  done

  local hint=""
  for flag in "$@"; do
    if [[ -n "${hint}" ]]; then
      hint="${hint} or "
    fi
    hint="${hint}${flag}=1"
  done

  cat >&2 <<EOF
This command may ${description}.
Set ${hint} only when this action is deliberate and explicitly approved.
EOF
  exit 2
}
