#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -z "${AUTOWARE_COMMAND:-}" || "${AUTOWARE_COMMAND}" == REPLACE_* ]]; then
  echo "AUTOWARE_COMMAND is not configured" >&2
  exit 78
fi

exec bash -lc "${AUTOWARE_COMMAND}"
