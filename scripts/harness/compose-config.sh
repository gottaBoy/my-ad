#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${repo_root}"

env_file="${ENV_FILE:-.env}"
[[ -f "${env_file}" ]] || {
  echo "env file not found: ${env_file}" >&2
  exit 66
}

if [[ -n "${ARTIFACT_DIR:-}" ]]; then
  artifact_dir="${ARTIFACT_DIR}"
else
  run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  artifact_dir="${ARTIFACT_ROOT:-artifacts}/compose-config/${run_stamp}"
fi

mkdir -p "${artifact_dir}"
docker compose --env-file "${env_file}" -f compose.dgx.yaml \
  --profile "*" config > "${artifact_dir}/dgx.yaml"
docker compose --env-file "${env_file}" -f compose.sim-x86.yaml \
  --profile "*" config > "${artifact_dir}/sim-x86.yaml"
docker compose --env-file "${env_file}" -f compose.dgx.yaml \
  --profile "*" config --images > "${artifact_dir}/dgx-images.txt"
docker compose --env-file "${env_file}" -f compose.sim-x86.yaml \
  --profile "*" config --images > "${artifact_dir}/sim-x86-images.txt"
printf 'PASS compose configs written to %s\n' "${artifact_dir}"
