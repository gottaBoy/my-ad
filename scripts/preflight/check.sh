#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${repo_root}"

env_file="${ENV_FILE:-.env}"
failures=0

pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
warn() { printf 'WARN %s\n' "$1" >&2; }

# shellcheck source=../lib/load-env.sh
source "${repo_root}/scripts/lib/load-env.sh"
load_env_file "${env_file}" || exit 2

role="${HOST_ROLE:-}"
scope="${PREFLIGHT_SCOPE:-core}"
deployment_mode="${DEPLOYMENT_MODE:-validation}"

is_placeholder() {
  local value="${1:-}"
  [[ -z "${value}" || "${value}" == REPLACE_* ]]
}

require_var() {
  local name="$1"
  local value="${!name:-}"
  if is_placeholder "${value}"; then
    fail "${name} is not configured"
  else
    pass "${name} configured"
  fi
}

[[ -n "${role}" ]] || fail "HOST_ROLE must be dgx or sim-x86"
[[ "${role}" == "dgx" || "${role}" == "sim-x86" ]] || fail "HOST_ROLE=$role is invalid"
[[ "${scope}" == "host" || "${scope}" == "core" || "${scope}" == "all" ]] \
  || fail "PREFLIGHT_SCOPE=$scope must be host, core, or all"
[[ "${deployment_mode}" == "validation" || "${deployment_mode}" == "production" ]] \
  || fail "DEPLOYMENT_MODE=$deployment_mode must be validation or production"

arch="$(uname -m)"
os_name="$(uname -s)"
[[ "${os_name}" == "Linux" ]] \
  && pass "host operating system Linux" \
  || fail "target deployment requires Linux, got ${os_name}"

if [[ "${role}" == "dgx" ]]; then
  [[ "${arch}" == "aarch64" || "${arch}" == "arm64" ]] && pass "DGX host architecture ${arch}" || fail "DGX requires aarch64, got ${arch}"
else
  [[ "${arch}" == "x86_64" || "${arch}" == "amd64" ]] && pass "simulation host architecture ${arch}" || fail "simulation host requires x86_64, got ${arch}"
fi

if [[ -r /proc/meminfo ]]; then
  memory_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  if [[ "${role}" == "dgx" ]]; then
    required_memory_gb="${DGX_MIN_HOST_MEMORY_GB:-100}"
  else
    required_memory_gb="${SIM_MIN_HOST_MEMORY_GB:-32}"
  fi
  required_memory_kb="$((required_memory_gb * 1024 * 1024))"
  [[ "${memory_kb}" -ge "${required_memory_kb}" ]] \
    && pass "host memory is at least ${required_memory_gb} GiB" \
    || fail "host memory is below ${required_memory_gb} GiB"
else
  fail "/proc/meminfo is unavailable"
fi

if command -v docker >/dev/null 2>&1; then
  pass "docker installed"
  docker compose version >/dev/null 2>&1 \
    && pass "docker compose v2 available" \
    || fail "docker compose v2 is not available"
  docker info >/dev/null 2>&1 \
    && pass "docker daemon reachable" \
    || fail "docker daemon is not reachable"
else
  fail "docker is not installed"
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi -L >/dev/null 2>&1 && pass "NVIDIA GPU visible" || fail "NVIDIA GPU is not usable"
else
  fail "nvidia-smi is not installed"
fi

paths=(
  "${MAPS_DIR:-./data/maps}"
  "${BAGS_DIR:-./data/bags}"
  "${GROUND_TRUTH_DIR:-./data/ground_truth}"
  "${DATASETS_DIR:-./data/datasets}"
  "${MODELS_DIR:-./data/models}"
  "${ENGINES_DIR:-./data/engines}"
  "${LOGS_DIR:-./data/logs}"
  "${REPORTS_DIR:-./data/reports}"
  "${CACHE_DIR:-./data/cache}"
  "${ARTIFACT_ROOT:-./artifacts}"
)

for path in "${paths[@]}"; do
  mkdir -p "${path}"
  [[ -w "${path}" ]] && pass "writable ${path}" || fail "not writable ${path}"
done

if [[ "${scope}" != "host" ]]; then
  require_var ROS_DOMAIN_ID
  require_var ROS_DISTRO
  require_var RMW_IMPLEMENTATION
  require_var GPU_SMOKE_IMAGE

  if [[ "${role}" == "dgx" ]]; then
    require_var AUTOWARE_IMAGE
    require_var AUTOWARE_COMMAND
    require_var AUTOWARE_HEALTHCHECK_COMMAND
    compose_file="compose.dgx.yaml"
  else
    require_var AWSIM_IMAGE
    require_var AWSIM_COMMAND
    require_var AWSIM_HEALTHCHECK_COMMAND
    compose_file="compose.sim-x86.yaml"
  fi

  if [[ "${REQUIRE_MAP_DATA:-1}" == "1" ]]; then
    map_dir="${MAPS_DIR:-./data/maps}"
    if [[ "${role}" == "dgx" ]]; then
      if [[ -s "${map_dir}/lanelet2_map.osm" && -s "${map_dir}/pointcloud_map.pcd" ]]; then
        pass "Autoware map files present in ${map_dir}"
      else
        fail "expected lanelet2_map.osm and pointcloud_map.pcd in ${map_dir}"
      fi
    elif find "${map_dir}" -mindepth 1 -print -quit | grep -q .; then
      pass "map data present in ${map_dir}"
    else
      fail "map data is empty in ${map_dir}"
    fi
  fi

  if docker compose --env-file "${env_file}" -f "${compose_file}" \
    --profile "*" config --quiet; then
    pass "${compose_file} expands with all profiles"
  else
    fail "${compose_file} failed Compose expansion"
  fi
fi

placeholder_count="$(grep -c 'REPLACE_' "${env_file}" || true)"
if [[ "${scope}" == "all" && "${placeholder_count}" -gt 0 ]]; then
  fail "${env_file} still contains ${placeholder_count} REPLACE_ values"
elif [[ "${placeholder_count}" -gt 0 ]]; then
  warn "${env_file} contains ${placeholder_count} optional or out-of-scope REPLACE_ values"
fi

if [[ "${deployment_mode}" == "production" && "${scope}" != "host" ]]; then
  if [[ "${role}" == "dgx" ]]; then
    production_image="${AUTOWARE_IMAGE:-}"
  else
    production_image="${AWSIM_IMAGE:-}"
  fi
  [[ "${production_image}" == *@sha256:* ]] \
    && pass "core image is pinned by digest" \
    || fail "production core image must be pinned with @sha256 digest"
fi

data_root="${DATA_ROOT:-./data}"
if [[ -n "${MIN_FREE_DISK_GB:-}" && -d "${data_root}" ]]; then
  available_kb="$(df -Pk "${data_root}" | awk 'NR==2 {print $4}')"
  required_kb="$((MIN_FREE_DISK_GB * 1024 * 1024))"
  [[ "${available_kb}" -ge "${required_kb}" ]] \
    && pass "free disk is at least ${MIN_FREE_DISK_GB} GiB" \
    || fail "free disk is below ${MIN_FREE_DISK_GB} GiB"
fi

if [[ "${failures}" -gt 0 ]]; then
  printf 'BLOCKED preflight failures=%s\n' "${failures}" >&2
  exit 2
fi

printf 'PASS preflight role=%s scope=%s mode=%s\n' "${role}" "${scope}" "${deployment_mode}"
