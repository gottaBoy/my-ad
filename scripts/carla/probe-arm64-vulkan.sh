#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

timeout_seconds="${CARLA_VULKAN_TIMEOUT:-45}"
[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || { echo "CARLA_VULKAN_TIMEOUT must be positive" >&2; exit 64; }
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this probe in native ARM64 Docker with NVIDIA graphics capabilities" >&2
  exit 2
fi
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/vulkan-readback-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
scope="Native GB10 Vulkan render-pass clear/readback; not UE shaders or CARLA sensors"
finish() {
  local code=$?
  if [[ ! -s "${run_dir}/stage-report.json" ]]; then
    python3 "${script_dir}/stage_report.py" write --output "${run_dir}/stage-report.json" \
      --stage-id dgx-vulkan-readback --scope "${scope}" --exit-code "${code}" \
      --required-check build --check build FAIL --evidence build "${run_dir}/build.log" \
      --source probe "${script_dir}/vulkan-readback.c" source-build \
      --command bash "${BASH_SOURCE[0]}" > "${run_dir}/report-error.log" 2>&1 || true
  fi
  printf "vulkan-readback artifacts=%s exit=%s\n" "${run_dir}" "${code}"
}
trap finish EXIT
export XDG_RUNTIME_DIR="${run_dir}/xdg"
mkdir -m 0700 "${XDG_RUNTIME_DIR}"
command=(
  clang-18 -std=c11 -O2 -Wall -Wextra -Werror
  "${script_dir}/vulkan-readback.c" -o "${run_dir}/vulkan-readback" -lvulkan
)
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" > "${run_dir}/build-command.json"
sha256sum "${BASH_SOURCE[0]}" "${script_dir}/vulkan-readback.c" \
  "${script_dir}/check_vulkan_readback.py" "${script_dir}/stage_report.py" > "${run_dir}/inputs.sha256"
if ! "${command[@]}" > "${run_dir}/build.log" 2>&1; then
  tail -n 80 "${run_dir}/build.log"
  exit 1
fi
sha256sum --check "${run_dir}/inputs.sha256"
python3 "${script_dir}/check_vulkan_readback.py" --program "${run_dir}/vulkan-readback" \
  --run-dir "${run_dir}" --timeout "${timeout_seconds}"
python3 "${script_dir}/stage_report.py" validate "${run_dir}/stage-report.json" \
  --stage-id dgx-vulkan-readback --scope "${scope}"
