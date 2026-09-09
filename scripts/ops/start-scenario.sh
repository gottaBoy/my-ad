#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${SCENARIO_COMMAND:-}" == "" ]]; then
  echo "SCENARIO_COMMAND is not configured" >&2
  exit 78
fi

exec bash -lc "${SCENARIO_COMMAND}"
