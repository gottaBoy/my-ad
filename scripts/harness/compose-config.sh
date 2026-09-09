#!/usr/bin/env bash
set -Eeuo pipefail

env_file="${ENV_FILE:-.env}"
[[ -f "${env_file}" ]] || {
  echo "env file not found: ${env_file}" >&2
  exit 66
}

mkdir -p artifacts/compose-config
docker compose --env-file "${env_file}" -f compose.dgx.yaml config > artifacts/compose-config/dgx.yaml
docker compose --env-file "${env_file}" -f compose.sim-x86.yaml config > artifacts/compose-config/sim-x86.yaml
printf 'PASS compose configs written to artifacts/compose-config\n'
