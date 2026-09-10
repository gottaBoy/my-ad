#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -z "${AWSIM_COMMAND:-}" || "${AWSIM_COMMAND}" == REPLACE_* ]]; then
  echo "AWSIM_COMMAND is not configured" >&2
  exit 78
fi

exec bash -lc "${AWSIM_COMMAND}"
