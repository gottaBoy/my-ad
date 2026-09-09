#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${GROUND_TRUTH_COMMAND:-}" == "" ]]; then
  echo "GROUND_TRUTH_COMMAND is not configured for this AWSIM message schema" >&2
  exit 78
fi

exec bash -lc "${GROUND_TRUTH_COMMAND}"
