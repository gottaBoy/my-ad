#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -z "${FOXGLOVE_COMMAND:-}" || "${FOXGLOVE_COMMAND}" == REPLACE_* ]]; then
  echo "FOXGLOVE_COMMAND is not configured" >&2
  exit 78
fi

exec bash -lc "${FOXGLOVE_COMMAND}"
