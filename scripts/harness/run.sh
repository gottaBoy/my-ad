#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${repo_root}"

env_file="${ENV_FILE:-.env}"
# shellcheck source=../lib/load-env.sh
source "${repo_root}/scripts/lib/load-env.sh"
load_env_file "${env_file}" || {
  echo "BLOCKED unable to load env file: ${env_file}" >&2
  exit 2
}

test_id="${1:-}"
role="${HOST_ROLE:-unknown}"
run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
host_id="$(hostname -s 2>/dev/null || hostname)"
artifact_dir="${ARTIFACT_ROOT:-artifacts}/${test_id:-manual}/${run_stamp}-${host_id}"
finished=0

mkdir -p \
  "${artifact_dir}/host-info" \
  "${artifact_dir}/image-info" \
  "${artifact_dir}/compose-config" \
  "${artifact_dir}/logs" \
  "${artifact_dir}/metrics"

write_decision() {
  local status="$1"
  local message="$2"
  cat > "${artifact_dir}/decision.md" <<EOF
# Harness Decision

- Test: ${test_id:-manual}
- Status: ${status}
- Host role: ${role}
- Timestamp UTC: ${run_stamp}
- Evidence: ${artifact_dir}
- Detail: ${message}
EOF
}

finish() {
  local status="$1"
  local message="$2"
  local code="${3:-0}"
  finished=1
  write_decision "${status}" "${message}"
  printf '%s harness test=%s artifacts=%s detail=%s\n' \
    "${status}" "${test_id:-manual}" "${artifact_dir}" "${message}"
  trap - EXIT
  exit "${code}"
}

on_exit() {
  local code=$?
  if [[ "${finished}" == "0" ]]; then
    write_decision "FAIL" "unhandled command failure with exit code ${code}"
  fi
}
trap on_exit EXIT

require_command() {
  local command_name="$1"
  command -v "${command_name}" >/dev/null 2>&1 \
    || finish "BLOCKED" "required command is unavailable: ${command_name}" 2
}

is_placeholder() {
  local value="${1:-}"
  [[ -z "${value}" || "${value}" == REPLACE_* ]]
}

require_setting() {
  local name="$1"
  local value="${!name:-}"
  if is_placeholder "${value}"; then
    finish "BLOCKED" "required setting is not configured: ${name}" 2
  fi
  return 0
}

expected_arch() {
  if [[ "${role}" == "dgx" ]]; then
    printf 'arm64\n'
  elif [[ "${role}" == "sim-x86" ]]; then
    printf 'amd64\n'
  else
    finish "BLOCKED" "HOST_ROLE must be dgx or sim-x86" 2
  fi
}

case "${test_id}" in
  host)
    require_command docker
    uname -a | tee "${artifact_dir}/host-info/uname.txt"
    docker version | tee "${artifact_dir}/host-info/docker-version.txt"
    docker compose version | tee "${artifact_dir}/host-info/compose-version.txt"
    docker info | tee "${artifact_dir}/host-info/docker-info.txt"

    actual_arch="$(docker info --format '{{.Architecture}}')"
    required_arch="$(expected_arch)"
    case "${required_arch}:${actual_arch}" in
      arm64:aarch64|arm64:arm64|amd64:x86_64|amd64:amd64) ;;
      *) finish "FAIL" "Docker host architecture ${actual_arch} does not match ${required_arch}" 1 ;;
    esac
    finish "PASS" "host architecture and Docker daemon verified"
    ;;

  gpu)
    require_command docker
    image="${GPU_SMOKE_IMAGE:-}"
    command_value="${GPU_SMOKE_COMMAND:-}"
    is_placeholder "${image}" \
      && finish "BLOCKED" "GPU_SMOKE_IMAGE is not configured" 2
    is_placeholder "${command_value}" \
      && finish "BLOCKED" "GPU_SMOKE_COMMAND is not configured" 2

    if ! docker image inspect "${image}" >/dev/null 2>&1; then
      docker pull "${image}" \
        | tee "${artifact_dir}/logs/gpu-smoke-pull.txt" \
        || finish "BLOCKED" "GPU smoke image cannot be pulled" 2
    fi
    docker image inspect "${image}" \
      --format '{{json .RepoDigests}} {{.Architecture}}' \
      | tee "${artifact_dir}/image-info/gpu-smoke-image.txt"

    required_arch="$(expected_arch)"
    docker run --rm --gpus all --platform "linux/${required_arch}" "${image}" \
      bash -lc "${command_value}" \
      | tee "${artifact_dir}/logs/gpu-smoke.txt"
    finish "PASS" "GPU container completed configured CUDA computation"
    ;;

  navsim)
    require_command docker
    [[ "${role}" == "dgx" ]] \
      || finish "BLOCKED" "NAVSIM profile is intended for the DGX Spark host" 2

    for setting in \
      NAVSIM_IMAGE \
      NAVSIM_GIT_REF \
      NAVSIM_COMMAND \
      NAVSIM_HEALTHCHECK_COMMAND \
      NAVSIM_SOURCE_DIR \
      NAVSIM_DATASET_DIR \
      NAVSIM_MAPS_DIR \
      NAVSIM_EXP_DIR \
      NAVSIM_MODEL_DIR \
      NAVSIM_REPORT_DIR \
      NAVSIM_CACHE_DIR \
      NUPLAN_MAP_VERSION \
      NUPLAN_MAPS_ROOT \
      NAVSIM_SPLIT; do
      require_setting "${setting}"
    done

    source_dir="${NAVSIM_SOURCE_DIR}"
    dataset_dir="${NAVSIM_DATASET_DIR}"
    maps_dir="${NAVSIM_MAPS_DIR}"
    for path in "${source_dir}" "${dataset_dir}" "${maps_dir}"; do
      [[ -d "${path}" ]] \
        || finish "BLOCKED" "NAVSIM path does not exist: ${path}" 2
    done
    [[ -f "${source_dir}/setup.py" || -f "${source_dir}/pyproject.toml" ]] \
      || finish "BLOCKED" "NAVSIM source tree is missing setup.py or pyproject.toml: ${source_dir}" 2
    find "${dataset_dir}" -mindepth 1 -print -quit | grep -q . \
      || finish "BLOCKED" "NAVSIM dataset directory is empty: ${dataset_dir}" 2
    find "${maps_dir}" -mindepth 1 -print -quit | grep -q . \
      || finish "BLOCKED" "NAVSIM maps directory is empty: ${maps_dir}" 2

    image="${NAVSIM_IMAGE}"
    if ! docker image inspect "${image}" >/dev/null 2>&1; then
      docker pull "${image}" \
        | tee "${artifact_dir}/logs/navsim-image-pull.txt" \
        || finish "BLOCKED" "NAVSIM image cannot be pulled: ${image}" 2
    fi
    docker image inspect "${image}" \
      --format '{{json .RepoDigests}} {{.Architecture}}' \
      | tee "${artifact_dir}/image-info/navsim-image.txt"
    navsim_arch="$(docker image inspect "${image}" --format '{{.Architecture}}')"
    case "${navsim_arch}" in
      arm64|aarch64) ;;
      *) finish "FAIL" "NAVSIM image architecture is ${navsim_arch}, expected arm64" 1 ;;
    esac
    if [[ "${DEPLOYMENT_MODE:-validation}" == "production" && "${image}" != *@sha256:* ]]; then
      finish "BLOCKED" "production NAVSIM image must be pinned with @sha256 digest" 2
    fi

    docker compose --env-file "${env_file}" -f compose.dgx.yaml \
      --profile navsim config > "${artifact_dir}/compose-config/navsim.yaml" \
      || finish "BLOCKED" "NAVSIM Compose profile failed to expand" 2
    docker compose --env-file "${env_file}" -f compose.dgx.yaml \
      --profile navsim run --rm --no-deps navsim \
      /opt/my-ad/scripts/ops/healthcheck-command.sh NAVSIM_HEALTHCHECK_COMMAND \
      | tee "${artifact_dir}/metrics/navsim-healthcheck.txt" \
      || finish "FAIL" "NAVSIM image, source, dependency, or data healthcheck failed" 1
    finish "PASS" "NAVSIM image, source tree, dataset, maps, and import healthcheck verified"
    ;;

  network)
    require_command ping
    require_command iperf3
    require_command python3

    if [[ "${role}" == "dgx" ]]; then
      peer="${SIM_HOST_ADDR:-}"
    elif [[ "${role}" == "sim-x86" ]]; then
      peer="${DGX_HOST_ADDR:-}"
    else
      finish "BLOCKED" "HOST_ROLE must be dgx or sim-x86" 2
    fi
    is_placeholder "${peer}" \
      && finish "BLOCKED" "peer host address is not configured" 2
    require_setting MIN_NETWORK_THROUGHPUT_MBPS
    require_setting MAX_NETWORK_JITTER_MS
    require_setting MAX_NETWORK_PACKET_LOSS_PCT

    LC_ALL=C ping -c 10 -W 2 "${peer}" \
      | tee "${artifact_dir}/metrics/ping.txt"
    if ! iperf3 -c "${peer}" \
      -p "${IPERF_PORT:-5201}" \
      -t "${IPERF_DURATION_SEC:-10}" \
      --json > "${artifact_dir}/metrics/iperf3.json"; then
      finish "BLOCKED" "iperf3 server is unavailable on ${peer}:${IPERF_PORT:-5201}" 2
    fi

    python3 scripts/harness/evaluate-network.py \
      --ping "${artifact_dir}/metrics/ping.txt" \
      --iperf "${artifact_dir}/metrics/iperf3.json" \
      --min-throughput-mbps "${MIN_NETWORK_THROUGHPUT_MBPS}" \
      --max-jitter-ms "${MAX_NETWORK_JITTER_MS}" \
      --max-loss-pct "${MAX_NETWORK_PACKET_LOSS_PCT}" \
      | tee "${artifact_dir}/metrics/network-evaluation.txt"
    finish "PASS" "peer reachability, loss, jitter, and throughput meet thresholds"
    ;;

  clock)
    require_command chronyc
    require_command python3
    require_setting TIME_SYNC_MAX_OFFSET_MS
    tracking_file="${artifact_dir}/metrics/chrony-tracking.txt"
    evaluation_file="${artifact_dir}/metrics/clock-evaluation.txt"

    if ! LC_ALL=C chronyc tracking >"${tracking_file}" 2>&1; then
      cat "${tracking_file}" >&2
      finish "FAIL" "chronyc tracking failed" 1
    fi
    cat "${tracking_file}"

    if ! python3 scripts/harness/evaluate-clock.py \
      --tracking "${tracking_file}" \
      --max-offset-ms "${TIME_SYNC_MAX_OFFSET_MS}" \
      >"${evaluation_file}" 2>&1; then
      cat "${evaluation_file}" >&2
      finish "FAIL" "clock synchronization evaluation failed" 1
    fi
    cat "${evaluation_file}"
    finish "PASS" "clock synchronization offset meets threshold"
    ;;

  runtime)
    require_command docker
    service="${2:-}"
    [[ -n "${service}" ]] \
      || finish "BLOCKED" "runtime test requires a Compose service name" 2

    if [[ "${role}" == "dgx" ]]; then
      compose_file="compose.dgx.yaml"
    elif [[ "${role}" == "sim-x86" ]]; then
      compose_file="compose.sim-x86.yaml"
    else
      finish "BLOCKED" "HOST_ROLE must be dgx or sim-x86" 2
    fi

    container_id="$(
      docker compose --env-file "${env_file}" -f "${compose_file}" \
        --profile "*" ps -q "${service}"
    )"
    [[ -n "${container_id}" ]] \
      || finish "FAIL" "Compose service is not running: ${service}" 1

    docker inspect --format '{{json .State}}' "${container_id}" \
      > "${artifact_dir}/logs/${service}-state.json"
    docker inspect --format '{{.Config.Image}} {{.Image}}' "${container_id}" \
      > "${artifact_dir}/image-info/${service}-image.txt"
    docker logs "${container_id}" \
      > "${artifact_dir}/logs/${service}.log" 2>&1

    container_status="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
    health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "${container_id}")"
    printf 'container_status=%s\nhealth_status=%s\n' \
      "${container_status}" "${health_status}" \
      | tee "${artifact_dir}/metrics/${service}-status.txt"

    [[ "${container_status}" == "running" ]] \
      || finish "FAIL" "${service} container status is ${container_status}" 1
    [[ "${health_status}" == "healthy" ]] \
      || finish "FAIL" "${service} health status is ${health_status}" 1
    finish "PASS" "${service} is running and its configured business probe is healthy"
    ;;

  ros)
    require_command docker
    [[ "${role}" == "dgx" ]] \
      || finish "BLOCKED" "ROS discovery probe currently runs from the DGX Compose stack" 2

    topic_file="${2:-/config/harness/required-topics.txt}"
    docker compose --env-file "${env_file}" -f compose.dgx.yaml \
      --profile harness run --rm --no-deps ros-probe \
      /opt/my-ad/scripts/harness/check-ros-topics.sh "${topic_file}" \
      | tee "${artifact_dir}/metrics/ros-topics.txt"
    finish "PASS" "required ROS topics were discovered before timeout"
    ;;

  compose)
    ARTIFACT_DIR="${artifact_dir}/compose-config" \
      ENV_FILE="${env_file}" \
      ./scripts/harness/compose-config.sh \
      | tee "${artifact_dir}/logs/compose-config.txt"
    finish "PASS" "all Compose profiles expanded successfully"
    ;;

  *)
    cat >&2 <<'EOF'
Usage:
  scripts/harness/run.sh host|gpu|navsim|network|clock|compose
  scripts/harness/run.sh runtime <compose-service>
  scripts/harness/run.sh ros [topic-file-in-container]
  scripts/harness/run.sh ros /config/harness/required-topics-scenario.txt

Runtime integration and e2e tests require target containers and real ROS
evidence. This script does not report those stages as PASS without evidence.
EOF
    finish "BLOCKED" "unknown or unimplemented Harness test: ${test_id:-empty}" 2
    ;;
esac
