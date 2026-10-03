#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

client_root="${CARLA_COOKED_CLIENT_ROOT:-/artifacts/carla/cooked-client-full/CarlaUnreal}"
binary="${client_root}/Binaries/LinuxArm64/CarlaUnreal"
debug_binary="${CARLA_DEBUG_BINARY:-/workspace/carla/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal.debug}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
port="${CARLA_UE_DEVICE_STATE_PORT:-20207}"
timeout_seconds="${CARLA_UE_DEVICE_STATE_TIMEOUT:-90}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
gdb_script="${script_dir}/dump-ue-vulkan-device-state.gdb"
vk_icd="${CARLA_VK_ICD_FILENAMES:-}"
source "${script_dir}/arm64-vulkan-diagnostic-scope.sh"

[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run this probe in native ARM64 Docker" >&2
  exit 2
}
[[ -x "${binary}" && -f "${debug_binary}" && -f "${gdb_script}" ]] || {
  echo "Client, debug binary or GDB script is missing" >&2
  exit 2
}
[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ && "${timeout_seconds}" -le 300 ]] || {
  echo "CARLA_UE_DEVICE_STATE_TIMEOUT must be 1..300" >&2
  exit 64
}
[[ "${port}" =~ ^[1-9][0-9]*$ && "${port}" -ge 1024 && "${port}" -le 65532 ]] || {
  echo "CARLA_UE_DEVICE_STATE_PORT must be 1024..65532" >&2
  exit 64
}
if [[ -z "${vk_icd}" ]]; then
  vk_icd="$(find /usr/share/vulkan/icd.d -maxdepth 1 -type f -name 'lvp_icd*.json' -print -quit)"
fi
[[ -f "${vk_icd}" ]] || {
  echo "Vulkan ICD is missing: ${vk_icd}" >&2
  exit 2
}
command -v gdb >/dev/null 2>&1 || {
  echo "gdb is required for the device-state probe" >&2
  exit 2
}

run_dir="$(mktemp -d "${artifact_dir}/ue-vulkan-device-state-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
export CARLA_UE_VULKAN_DEVICE_STATE="${run_dir}/device-create.json"
export CARLA_GDB_SCRIPTS="${run_dir}"
step=capture
finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 && "${step}" == complete ]] && status=PASS
  printf "# UE Vulkan Device State\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Scope: diagnostic snapshot before VkDeviceCreateInfo submission; no runtime acceptance\n- Device state: %s\n" \
    "${status}" "${step}" "${code}" "${run_dir}/device-create.json" > "${run_dir}/decision.md"
  printf "%s ue-vulkan-device-state artifacts=%s step=%s exit=%s\n" "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

client_args=(
  /Game/Carla/Maps/Town10HD_Opt -carla-rpc-port="${port}"
)
while IFS= read -r renderer_flag; do
  client_args+=("${renderer_flag}")
done < <(carla_vulkan_diagnostic_flags)
gdb_command=(
  timeout --signal=TERM --kill-after=5 "${timeout_seconds}"
  gdb -q -nx -batch
  -ex "file ${binary}" -ex "symbol-file ${debug_binary}"
  -x "${run_dir}/dump-ue-vulkan-device-state.gdb" -ex run -ex quit --args "${binary}" "${client_args[@]}"
)
cp -a "${BASH_SOURCE[0]}" "${gdb_script}" \
  "${script_dir}/gdb_vulkan_device_capture.py" "${script_dir}/vulkan_device_snapshot.py" \
  "${script_dir}/arm64-renderer-scope.sh" "${run_dir}/"
cp -a "${script_dir}/arm64-vulkan-diagnostic-scope.sh" "${run_dir}/"
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${gdb_command[@]}" \
  > "${run_dir}/command.json"
sha256sum "${run_dir}/probe-ue-vulkan-device-state.sh" \
  "${run_dir}/dump-ue-vulkan-device-state.gdb" \
  "${run_dir}/gdb_vulkan_device_capture.py" "${run_dir}/vulkan_device_snapshot.py" \
  "${run_dir}/arm64-renderer-scope.sh" "${run_dir}/command.json" \
  "${run_dir}/arm64-vulkan-diagnostic-scope.sh" \
  "${binary}" "${debug_binary}" "${vk_icd}" > "${run_dir}/inputs.sha256"
env=(
  VK_ICD_FILENAMES="${vk_icd}"
  XDG_RUNTIME_DIR="${run_dir}/xdg"
)
mkdir -m 0700 "${run_dir}/xdg"
(
  cd "${client_root}"
  env "${env[@]}" "${gdb_command[@]}"
) > "${run_dir}/gdb.log" 2>&1

step=validate
[[ -s "${run_dir}/device-create.json" ]] || {
  echo "UE device state was not captured: ${run_dir}" >&2
  exit 3
}
python3 "${run_dir}/vulkan_device_snapshot.py" --input "${run_dir}/device-create.json" \
  > "${run_dir}/validation.log" 2>&1
sha256sum "${run_dir}/device-create.json" >> "${run_dir}/inputs.sha256"
step=complete
