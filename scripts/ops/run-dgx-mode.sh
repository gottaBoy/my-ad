#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${repo_root}"

env_file="${ENV_FILE:-.env}"
mode="${1:-}"
# shellcheck source=../lib/load-env.sh
source "${repo_root}/scripts/lib/load-env.sh"
load_env_file "${env_file}" || {
  echo "unable to load env file: ${env_file}" >&2
  exit 2
}

[[ "${HOST_ROLE:-}" == "dgx" ]] || {
  echo "this mode runner requires HOST_ROLE=dgx" >&2
  exit 64
}
command -v docker >/dev/null 2>&1 || {
  echo "docker is required" >&2
  exit 69
}

compose=(docker compose --env-file "${env_file}" -f compose.dgx.yaml)

is_placeholder() {
  local value="${1:-}"
  [[ -z "${value}" || "${value}" == REPLACE_* ]]
}

require_value() {
  local name="$1"
  local value="${!name:-}"
  if is_placeholder "${value}"; then
    echo "${name} is not configured" >&2
    exit 78
  fi
  return 0
}

require_autoware_map() {
  local map_dir="${MAPS_DIR:-./data/maps}"
  [[ -s "${map_dir}/lanelet2_map.osm" && -s "${map_dir}/pointcloud_map.pcd" ]] || {
    echo "both lanelet2_map.osm and pointcloud_map.pcd are required under ${map_dir}" >&2
    exit 66
  }
}

wait_for_healthy() {
  local service="$1"
  local timeout_sec="${2:-180}"
  local container_id=""
  local state=""
  local health=""
  local deadline=$((SECONDS + timeout_sec))

  while (( SECONDS < deadline )); do
    container_id="$("${compose[@]}" ps -q "${service}" 2>/dev/null || true)"
    if [[ -n "${container_id}" ]]; then
      state="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "${container_id}")"
      if [[ "${health}" == "healthy" ]]; then
        return 0
      fi
      if [[ "${state}" == "exited" || "${state}" == "dead" || "${health}" == "unhealthy" ]]; then
        "${compose[@]}" logs --no-color --tail=100 "${service}" >&2 || true
        echo "${service} did not become healthy: state=${state} health=${health}" >&2
        return 1
      fi
    fi
    sleep 2
  done

  "${compose[@]}" logs --no-color --tail=100 "${service}" >&2 || true
  echo "timed out waiting for ${service} to become healthy" >&2
  return 1
}

planning_command="${AUTOWARE_PLANNING_COMMAND:-${AUTOWARE_COMMAND:-}}"
planning_healthcheck="${AUTOWARE_PLANNING_HEALTHCHECK_COMMAND:-${AUTOWARE_HEALTHCHECK_COMMAND:-}}"
scenario_autoware_command="${AUTOWARE_SCENARIO_COMMAND:-}"
scenario_autoware_healthcheck="${AUTOWARE_SCENARIO_HEALTHCHECK_COMMAND:-${AUTOWARE_HEALTHCHECK_COMMAND:-}}"

case "${mode}" in
  planning)
    is_placeholder "${planning_command}" && {
      echo "AUTOWARE_PLANNING_COMMAND or AUTOWARE_COMMAND is not configured" >&2
      exit 78
    }
    is_placeholder "${planning_healthcheck}" && {
      echo "AUTOWARE_PLANNING_HEALTHCHECK_COMMAND or AUTOWARE_HEALTHCHECK_COMMAND is not configured" >&2
      exit 78
    }
    require_autoware_map
    AUTOWARE_COMMAND="${planning_command}" \
    AUTOWARE_HEALTHCHECK_COMMAND="${planning_healthcheck}" \
      "${compose[@]}" up -d --force-recreate autoware
    wait_for_healthy autoware
    echo "PASS DGX mode=planning autoware is healthy"
    ;;

  scenario-up)
    require_value SCENARIO_IMAGE
    require_value SCENARIO_COMMAND
    is_placeholder "${scenario_autoware_command}" && {
      echo "AUTOWARE_SCENARIO_COMMAND is not configured" >&2
      exit 78
    }
    is_placeholder "${scenario_autoware_healthcheck}" && {
      echo "AUTOWARE_SCENARIO_HEALTHCHECK_COMMAND is not configured" >&2
      exit 78
    }
    AUTOWARE_COMMAND="${scenario_autoware_command}" \
    AUTOWARE_HEALTHCHECK_COMMAND="${scenario_autoware_healthcheck}" \
    SCENARIO_COMMAND="${SCENARIO_COMMAND}" \
      "${compose[@]}" --profile scenario up -d --force-recreate autoware scenario-simulator
    echo "Scenario Simulator stack started in detached mode"
    ;;

  scenario)
    require_value SCENARIO_IMAGE
    require_value SCENARIO_COMMAND
    is_placeholder "${scenario_autoware_command}" && {
      echo "AUTOWARE_SCENARIO_COMMAND is not configured" >&2
      exit 78
    }
    is_placeholder "${scenario_autoware_healthcheck}" && {
      echo "AUTOWARE_SCENARIO_HEALTHCHECK_COMMAND is not configured" >&2
      exit 78
    }
    scenario_file="${SCENARIO_FILE_HOST:-config/scenario/sample-scenario.yaml}"
    [[ -s "${scenario_file}" ]] || {
      echo "scenario file not found: ${scenario_file}; run make scenario-prepare" >&2
      exit 66
    }
    require_autoware_map
    mkdir -p data/reports/scenario

    AUTOWARE_COMMAND="${scenario_autoware_command}" \
    AUTOWARE_HEALTHCHECK_COMMAND="${scenario_autoware_healthcheck}" \
      "${compose[@]}" up -d --force-recreate autoware
    wait_for_healthy autoware

    AUTOWARE_COMMAND="${scenario_autoware_command}" \
    AUTOWARE_HEALTHCHECK_COMMAND="${scenario_autoware_healthcheck}" \
    SCENARIO_COMMAND="${SCENARIO_COMMAND}" \
      "${compose[@]}" --profile scenario up \
        --no-deps \
        --force-recreate \
        --abort-on-container-exit \
        --exit-code-from scenario-simulator \
        scenario-simulator
    ;;

  *)
    cat >&2 <<'EOF'
Usage:
  scripts/ops/run-dgx-mode.sh planning
  scripts/ops/run-dgx-mode.sh scenario-up
  scripts/ops/run-dgx-mode.sh scenario

planning   Start the validated Autoware planning_simulator baseline.
scenario-up
           Start Autoware plus Scenario Simulator in detached mode.
scenario   Start Autoware, wait for its business healthcheck, then run one
           Scenario Simulator scenario in the foreground.
EOF
    exit 64
    ;;
esac
