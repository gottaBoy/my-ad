#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -z "${ISAAC_COMMAND:-}" || "${ISAAC_COMMAND}" == REPLACE_* ]]; then
  echo "ISAAC_COMMAND is not configured" >&2
  exit 78
fi

exec bash -lc "${ISAAC_COMMAND}"
