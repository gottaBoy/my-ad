#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${ISAAC_COMMAND:-}" == "" ]]; then
  echo "ISAAC_COMMAND is not configured" >&2
  exit 78
fi

exec bash -lc "${ISAAC_COMMAND}"
