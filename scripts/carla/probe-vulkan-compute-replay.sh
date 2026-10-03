#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

backend="${CARLA_COMPUTE_BACKEND:-lavapipe}"
mode="${CARLA_COMPUTE_MODE:-smoke}"
shader="${CARLA_COMPUTE_SHADER:-}"
shader_dir="${CARLA_COMPUTE_SHADER_DIR:-}"
layout_file="${CARLA_COMPUTE_LAYOUT_FILE:-}"
device_state="${CARLA_COMPUTE_DEVICE_STATE:-}"
cache_file="${CARLA_COMPUTE_CACHE_FILE:-}"
cache_flags="${CARLA_COMPUTE_CACHE_FLAGS:-0}"
history_file="${CARLA_COMPUTE_HISTORY_FILE:-}"
timeout_seconds="${CARLA_COMPUTE_TIMEOUT:-30}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
reflect_root="${ue_dir}/Engine/Source/ThirdParty/SPIRV-Reflect/SPIRV-Reflect"
reflect_source="${reflect_root}/spirv_reflect.c"
reflect_include="${reflect_root}/include"
reflect_header="${reflect_root}/spirv_reflect.h"
fixture="${script_dir}/vulkan-compute-smoke.comp"

[[ "${backend}" == lavapipe || "${backend}" == gb10 ]] || {
  echo "CARLA_COMPUTE_BACKEND must be lavapipe or gb10" >&2
  exit 64
}
[[ "${mode}" == create || "${mode}" == smoke ]] || {
  echo "CARLA_COMPUTE_MODE must be create or smoke" >&2
  exit 64
}
[[ "${cache_flags}" == 0 || "${cache_flags}" == 1 ]] || {
  echo "CARLA_COMPUTE_CACHE_FLAGS must be 0 or 1" >&2; exit 64;
}
[[ -n "${cache_file}" || "${cache_flags}" == 0 ]] || {
  echo "Cache flags require CARLA_COMPUTE_CACHE_FILE" >&2; exit 64;
}
[[ -z "${cache_file}" || -f "${cache_file}" ]] || {
  echo "CARLA_COMPUTE_CACHE_FILE is missing: ${cache_file}" >&2; exit 2;
}
[[ -z "${history_file}" || -f "${history_file}" ]] || {
  echo "CARLA_COMPUTE_HISTORY_FILE is missing: ${history_file}" >&2; exit 2;
}
[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ && "${timeout_seconds}" -le 300 ]] || {
  echo "CARLA_COMPUTE_TIMEOUT must be a positive integer <= 300" >&2
  exit 64
}
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this probe in native ARM64 Docker" >&2
  exit 2
fi
[[ -f "${reflect_source}" && -f "${reflect_header}" && -d "${reflect_include}" ]] || {
  echo "SPIRV-Reflect source tree is missing: ${reflect_root}" >&2
  exit 2
}
if [[ -n "${shader_dir}" ]]; then
  [[ -d "${shader_dir}" ]] || { echo "CARLA_COMPUTE_SHADER_DIR is missing: ${shader_dir}" >&2; exit 2; }
  [[ -z "${shader}" && -z "${layout_file}" && -z "${device_state}" && -z "${cache_file}" && "${mode}" == create ]] || {
    echo "Batch create requires only CARLA_COMPUTE_SHADER_DIR and CARLA_COMPUTE_MODE=create" >&2
    exit 64
  }
fi

if [[ -n "${shader_dir}" ]]; then
  shader="batch:${shader_dir}"
elif [[ -n "${shader}" ]]; then
  [[ -f "${shader}" ]] || { echo "CARLA_COMPUTE_SHADER is missing: ${shader}" >&2; exit 2; }
  [[ "${mode}" == create ]] || {
    echo "CARLA_COMPUTE_MODE=smoke requires the built-in fixture shader" >&2
    exit 64
  }
fi
if [[ -n "${layout_file}" ]]; then
  [[ -f "${layout_file}" ]] || { echo "CARLA_COMPUTE_LAYOUT_FILE is missing: ${layout_file}" >&2; exit 2; }
  [[ "${mode}" == create ]] || { echo "UE layout override requires CARLA_COMPUTE_MODE=create" >&2; exit 64; }
fi
if [[ -n "${device_state}" ]]; then
  [[ -f "${device_state}" ]] || { echo "CARLA_COMPUTE_DEVICE_STATE is missing: ${device_state}" >&2; exit 2; }
fi

run_dir="$(mktemp -d "${artifact_dir}/vulkan-compute-replay-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 && "${step}" == complete ]] && status=PASS
  printf "# Vulkan Compute Replay\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Backend: %s\n- Mode: %s\n- Shader: %s\n- Scope: isolated compute pipeline; optional captured device configuration and UE layout; smoke verifies a fixture readback, not UE lifecycle or CARLA sensor acceptance\n" \
    "${status}" "${step}" "${code}" "${backend}" "${mode}" "${shader}" \
    > "${run_dir}/decision.md"
  printf "%s vulkan-compute-replay artifacts=%s step=%s exit=%s\n" \
    "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

[[ -x "$(command -v cc)" ]] || { echo "cc is required" >&2; exit 2; }
[[ -x "$(command -v spirv-val)" ]] || { echo "spirv-val is required" >&2; exit 2; }
[[ -f /usr/include/vulkan/vulkan.h ]] || { echo "Vulkan headers are missing" >&2; exit 2; }

step=build
cp -a "${BASH_SOURCE[0]}" "${run_dir}/probe.sh"
cp -a "${script_dir}/vulkan-compute-replay.c" "${run_dir}/"
cp -a "${fixture}" "${run_dir}/"
cp -a "${reflect_source}" "${run_dir}/"
cp -a "${reflect_header}" "${run_dir}/"
cp -a "${reflect_include}" "${run_dir}/include"
build_options=()
if [[ -n "${device_state}" ]]; then
  step=device-state
  cp -a "${device_state}" "${run_dir}/device-create.json"
  cp -a "${script_dir}/vulkan_device_snapshot.py" "${run_dir}/"
  python3 "${run_dir}/vulkan_device_snapshot.py" --input "${run_dir}/device-create.json" \
    --header "${run_dir}/captured-device.h" > "${run_dir}/device-validation.log" 2>&1
  sdk_include="${ue_dir}/Engine/Source/ThirdParty/Vulkan/Include"
  [[ -f "${sdk_include}/vulkan/vulkan.h" ]] || { echo "UE Vulkan SDK headers are missing" >&2; exit 2; }
  cp -a "${sdk_include}" "${run_dir}/vulkan-sdk"
  build_options+=(-DCARLA_USE_DEVICE_SNAPSHOT -I"${run_dir}/vulkan-sdk")
  step=build
fi
if [[ -n "${layout_file}" ]]; then
  cp -a "${layout_file}" "${run_dir}/ue-layout.txt"
  layout_file="${run_dir}/ue-layout.txt"
fi
if [[ -n "${cache_file}" ]]; then
  cp -a "${cache_file}" "${run_dir}/pipeline-cache-initial.bin"
  cache_file="${run_dir}/pipeline-cache-initial.bin"
fi
if [[ -n "${history_file}" ]]; then
  cp -a "${history_file}" "${run_dir}/history.txt"
  history_file="${run_dir}/history.txt"
fi
command=(
  cc -std=c11 -O2 -Wall -Wextra -Werror -Wno-unused-parameter
  "${build_options[@]}"
  -I/usr/include/vulkan
  -I"${run_dir}"
  -I"${run_dir}/include"
  "${run_dir}/vulkan-compute-replay.c"
  "${run_dir}/spirv_reflect.c"
  -lvulkan -o "${run_dir}/vulkan-compute-replay"
)
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" \
  > "${run_dir}/build-command.json"
"${command[@]}" > "${run_dir}/build.log" 2>&1

if [[ -z "${CARLA_COMPUTE_SHADER:-}" && -z "${shader_dir}" ]]; then
  step=fixture-compile
  shader="${run_dir}/shader.spv"
  glslangValidator -V "${run_dir}/vulkan-compute-smoke.comp" -o "${shader}" \
    > "${run_dir}/fixture-compile.log" 2>&1
elif [[ -z "${shader_dir}" ]]; then
  cp -a "${shader}" "${run_dir}/shader.spv"
  shader="${run_dir}/shader.spv"
fi
if [[ "${backend}" == lavapipe ]]; then
  lvp_icd="$(find /usr/share/vulkan/icd.d -maxdepth 1 -type f \
    -name 'lvp_icd*.json' -print -quit)"
  [[ -n "${lvp_icd}" ]] || {
    echo "Lavapipe ICD is missing from /usr/share/vulkan/icd.d" >&2
    exit 2
  }
fi
if [[ -z "${shader_dir}" ]]; then
  spirv-val --target-env vulkan1.3 "${shader}" \
    > "${run_dir}/spirv-validation.log" 2>&1
fi

step=run
sha256sum "${run_dir}/probe.sh" "${run_dir}/vulkan-compute-replay.c" \
  "${run_dir}/spirv_reflect.c" "${run_dir}/spirv_reflect.h" \
  "${run_dir}/build-command.json" > "${run_dir}/inputs.sha256"
if [[ -n "${device_state}" ]]; then
  sha256sum "${run_dir}/device-create.json" "${run_dir}/captured-device.h" \
    "${run_dir}/vulkan_device_snapshot.py" "${run_dir}/vulkan-sdk/vulkan/vulkan_core.h" \
    >> "${run_dir}/inputs.sha256"
fi
if [[ -n "${shader_dir}" ]]; then
  python3 "${script_dir}/batch_vulkan_compute.py" \
    --program "${run_dir}/vulkan-compute-replay" --validator "$(command -v spirv-val)" \
    --shader-dir "${shader_dir}" --run-dir "${run_dir}" --backend "${backend}" \
    --timeout "${timeout_seconds}" > "${run_dir}/batch.log" 2>&1 || {
      cat "${run_dir}/batch.log" >&2
      exit 1
    }
  cp -a "${script_dir}/batch_vulkan_compute.py" "${run_dir}/"
  sha256sum "${BASH_SOURCE[0]}" "${script_dir}/vulkan-compute-replay.c" \
    "${script_dir}/batch_vulkan_compute.py" "${reflect_source}" "${reflect_header}" \
    "${run_dir}/build-command.json" > "${run_dir}/inputs.sha256"
  step=complete
  exit 0
fi
command=(
  timeout --signal=TERM --kill-after=5 "${timeout_seconds}"
  "${run_dir}/vulkan-compute-replay" "${shader}" "${backend}" - "${mode}"
)
if [[ -n "${layout_file}" ]]; then
  command+=(--ue-layout "${layout_file}")
  sha256sum "${layout_file}" >> "${run_dir}/inputs.sha256"
fi
if [[ -n "${cache_file}" ]]; then
  command+=(--pipeline-cache "${cache_file}" "${cache_flags}")
  sha256sum "${cache_file}" >> "${run_dir}/inputs.sha256"
fi
if [[ -n "${history_file}" ]]; then
  command+=(--history "${history_file}")
  sha256sum "${history_file}" >> "${run_dir}/inputs.sha256"
fi
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" \
  > "${run_dir}/command.json"
sha256sum "${shader}" "${run_dir}/command.json" >> "${run_dir}/inputs.sha256"
run_env=()
if [[ "${backend}" == lavapipe ]]; then
  run_env+=(VK_ICD_FILENAMES="${lvp_icd}")
fi
env "${run_env[@]}" "${command[@]}" \
  > "${run_dir}/result.json" 2> "${run_dir}/run.log"
step=complete
