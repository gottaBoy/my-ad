#!/usr/bin/env bash
set -Eeuo pipefail

test_id="${1:-}"
artifact_dir="artifacts/${test_id:-manual}"
mkdir -p "${artifact_dir}/host-info" "${artifact_dir}/logs" "${artifact_dir}/metrics"

case "${test_id}" in
  T-00|host)
    uname -a | tee "${artifact_dir}/host-info/uname.txt"
    docker version | tee "${artifact_dir}/host-info/docker-version.txt"
    docker compose version | tee "${artifact_dir}/host-info/compose-version.txt"
    ;;
  T-01|gpu)
    : "${GPU_SMOKE_IMAGE:?GPU_SMOKE_IMAGE must be set}"
    docker run --rm --gpus all "${GPU_SMOKE_IMAGE}" \
      nvidia-smi | tee "${artifact_dir}/logs/gpu-smoke.txt"
    ;;
  T-05|network)
    : "${SIM_HOST_ADDR:?SIM_HOST_ADDR must be set}"
    : "${MIN_NETWORK_THROUGHPUT_MBPS:?MIN_NETWORK_THROUGHPUT_MBPS must be set}"
    if command -v ping >/dev/null 2>&1; then
      ping -c 5 "${SIM_HOST_ADDR}" | tee "${artifact_dir}/metrics/ping.txt"
    else
      printf 'BLOCKED ping command is unavailable\n' | tee "${artifact_dir}/metrics/network.txt"
      exit 2
    fi
    ;;
  compose)
    exec ./scripts/harness/compose-config.sh
    ;;
  *)
    cat >&2 <<'EOF'
Usage:
  scripts/harness/run.sh host|gpu|network|compose

Integration and e2e tests require the target containers and are intentionally
not reported as PASS by this script without their runtime evidence.
EOF
    exit 64
    ;;
esac

printf 'PASS harness test=%s artifacts=%s\n' "${test_id}" "${artifact_dir}"
