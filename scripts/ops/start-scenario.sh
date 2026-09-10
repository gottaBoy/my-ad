#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -z "${SCENARIO_COMMAND:-}" || "${SCENARIO_COMMAND}" == REPLACE_* ]]; then
  echo "SCENARIO_COMMAND is not configured" >&2
  exit 78
fi

exec bash -lc "${SCENARIO_COMMAND}"
