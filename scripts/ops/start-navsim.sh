#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -z "${NAVSIM_COMMAND:-}" || "${NAVSIM_COMMAND}" == REPLACE_* ]]; then
  echo "NAVSIM_COMMAND is not configured" >&2
  exit 78
fi

mkdir -p \
  "${NAVSIM_EXP_ROOT:-/data/navsim/exp}" \
  "${NAVSIM_REPORT_ROOT:-/data/reports/navsim}" \
  "${NAVSIM_CACHE_ROOT:-/data/cache/navsim}"

run_id="${NAVSIM_RUN_ID:-manual}"
[[ "${run_id}" =~ ^[A-Za-z0-9._-]+$ ]] || {
  echo "NAVSIM_RUN_ID must contain only letters, numbers, '.', '_' or '-'" >&2
  exit 64
}
run_dir="${NAVSIM_REPORT_ROOT:-/data/reports/navsim}/${run_id}"
mkdir -p "${run_dir}"
{
  printf 'NAVSIM_RUN_ID=%s\n' "${run_id}"
  printf 'NAVSIM_IMAGE=%s\n' "${NAVSIM_IMAGE:-unknown}"
  printf 'NAVSIM_REPO_URL=%s\n' "${NAVSIM_REPO_URL:-unknown}"
  printf 'NAVSIM_GIT_REF=%s\n' "${NAVSIM_GIT_REF:-unknown}"
  printf 'NAVSIM_SPLIT=%s\n' "${NAVSIM_SPLIT:-unknown}"
  printf 'NUPLAN_MAP_VERSION=%s\n' "${NUPLAN_MAP_VERSION:-unknown}"
  printf 'OPENSCENE_DATA_ROOT=%s\n' "${OPENSCENE_DATA_ROOT:-unknown}"
  printf 'NUPLAN_MAPS_ROOT=%s\n' "${NUPLAN_MAPS_ROOT:-unknown}"
  printf 'NAVSIM_SEED=%s\n' "${NAVSIM_SEED:-unknown}"
  printf 'NAVSIM_NUM_WORKERS=%s\n' "${NAVSIM_NUM_WORKERS:-unknown}"
  printf 'NAVSIM_BATCH_SIZE=%s\n' "${NAVSIM_BATCH_SIZE:-unknown}"
} > "${run_dir}/environment.env"
printf '%s\n' "${NAVSIM_COMMAND}" > "${run_dir}/command.txt"

exec bash -lc "${NAVSIM_COMMAND}"
