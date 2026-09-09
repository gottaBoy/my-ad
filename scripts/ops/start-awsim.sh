#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${AWSIM_COMMAND:-}" == "" ]]; then
  echo "AWSIM_COMMAND is not configured" >&2
  exit 78
fi

touch /tmp/awsim.ready
exec bash -lc "${AWSIM_COMMAND}"
