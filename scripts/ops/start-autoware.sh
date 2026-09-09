#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${AUTOWARE_COMMAND:-}" == "" ]]; then
  echo "AUTOWARE_COMMAND is not configured" >&2
  exit 78
fi

touch /tmp/autoware.ready
exec bash -lc "${AUTOWARE_COMMAND}"
