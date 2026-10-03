#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

client_root="${CARLA_COOKED_CLIENT_ROOT:-/artifacts/carla/cooked-client-full/CarlaUnreal}"
binary="${CARLA_UE_FAULT_BINARY:-${client_root}/Binaries/LinuxArm64/CarlaUnreal}"
debug_binary="${CARLA_DEBUG_BINARY:-/workspace/carla/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal.debug}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
port="${CARLA_UE_FAULT_PORT:-20231}"
timeout_seconds="${CARLA_UE_FAULT_TIMEOUT:-120}"
trace_gfx="${CARLA_UE_FAULT_TRACE_GFX:-0}"
memory_trace="${CARLA_UE_FAULT_MEMORY_TRACE:-0}"
icd="${CARLA_GB10_ICD:-/etc/vulkan/icd.d/nvidia_icd.json}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/arm64-vulkan-diagnostic-scope.sh"

[[ "${trace_gfx}" == 0 || "${trace_gfx}" == 1 ]] || {
  echo "CARLA_UE_FAULT_TRACE_GFX must be 0 or 1" >&2; exit 64;
}
[[ "${memory_trace}" == 0 || "${memory_trace}" == 1 ]] || {
  echo "CARLA_UE_FAULT_MEMORY_TRACE must be 0 or 1" >&2; exit 64;
}
[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ && ${#timeout_seconds} -le 3 && "${timeout_seconds}" -le 300 ]] || {
  echo "CARLA_UE_FAULT_TIMEOUT must be 1..300" >&2; exit 64;
}
[[ "${port}" =~ ^[1-9][0-9]*$ && ${#port} -le 5 && "${port}" -ge 1024 && "${port}" -le 65532 ]] || {
  echo "CARLA_UE_FAULT_PORT must be 1024..65532" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 && -x "${binary}" && -f "${debug_binary}" && -f "${icd}" ]] || {
  echo "Native ARM64 GPU Docker, staged binary, debug symbols and NVIDIA ICD required" >&2; exit 2;
}
command -v gdb >/dev/null
command -v vulkaninfo >/dev/null
python3 - "${port}" <<'PY'
import socket
import sys
listeners = []
for port in range(int(sys.argv[1]), int(sys.argv[1]) + 3):
    listener = socket.socket()
    # SO_REUSEADDR keeps an immediately preceding run's TIME_WAIT sockets from
    # failing a port that no process is actually listening on.
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", port))
    listeners.append(listener)
PY

run_dir="$(mktemp -d "${artifact_dir}/ue-vulkan-fault-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 && "${step}" == captured ]] && status=CAPTURED_FAULT
  printf "# UE First Vulkan Fault\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Graphics binding trace: %s (explicit, default off)\n- Scope: first Vulkan assertion or fatal signal, no creation API breakpoints or inferior function calls\n- Runtime acceptance: false; stored shader container is not a submitted-module replay input\n- Debug conditions: no-pso diagnostic profile, handled-ensure SIGTRAP suppressed; debugger scheduling differs; owned inferior killed on exit\n" \
    "${status}" "${step}" "${code}" "${trace_gfx}" > "${run_dir}/decision.md"
  printf -- "- Engine memory trace: %s (explicit, default off; requires diagnostic binary)\n" \
    "${memory_trace}" >> "${run_dir}/decision.md"
  printf "%s ue-vulkan-fault artifacts=%s step=%s exit=%s\n" "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT
cp -a "${BASH_SOURCE[0]}" "${script_dir}/gdb_vulkan_fault_capture.py" \
  "${script_dir}/dump-ue-vulkan-fault.gdb" "${script_dir}/vulkan_fault_snapshot.py" \
  "${script_dir}/arm64-vulkan-diagnostic-scope.sh" "${script_dir}/arm64-renderer-scope.sh" "${run_dir}/"
export VK_ICD_FILENAMES="${icd}"
export XDG_RUNTIME_DIR="${run_dir}/xdg"
mkdir -m 0700 "${XDG_RUNTIME_DIR}"
timeout --signal=TERM --kill-after=5 30 vulkaninfo --summary > "${run_dir}/vulkaninfo.log" 2>&1
grep -Eq '^[[:space:]]*deviceName[[:space:]]*=[[:space:]]*NVIDIA GB10[[:space:]]*$' "${run_dir}/vulkaninfo.log" || {
  echo "NVIDIA GB10 backend required" >&2; exit 3;
}
export CARLA_GDB_SCRIPTS="${run_dir}"
export CARLA_UE_FAULT_CAPTURE_DIR="${run_dir}"
export CARLA_UE_FAULT_TIMEOUT="${timeout_seconds}"
export CARLA_UE_FAULT_TRACE_GFX="${trace_gfx}"
export CARLA_UE_FAULT_MEMORY_TRACE="${memory_trace}"
client_args=(/Game/Carla/Maps/Town10HD_Opt -carla-rpc-port="${port}")
while IFS= read -r flag; do
  client_args+=("${flag}")
done < <(carla_vulkan_diagnostic_flags)
if [[ "${memory_trace}" == 1 ]]; then
  client_args+=("-CarlaVulkanGraphicsMemoryTrace=${run_dir}")
fi
command=(
  timeout --signal=TERM --kill-after=5 "$((timeout_seconds + 30))"
  gdb -q -nx -batch -ex "file ${binary}" -ex "symbol-file ${debug_binary}"
  -x "${run_dir}/dump-ue-vulkan-fault.gdb" -ex run
  -ex 'python gdb_vulkan_fault_capture.state.finish()' -ex quit
  --args "${binary}" "${client_args[@]}"
)
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" > "${run_dir}/command.json"
python3 -c 'import json,os; print(json.dumps({key:os.environ.get(key) for key in ("PATH","LD_LIBRARY_PATH","PYTHONHOME","VK_ICD_FILENAMES","CARLA_UE_FAULT_BINARY","CARLA_UE_FAULT_TIMEOUT","CARLA_UE_FAULT_TRACE_GFX","CARLA_UE_FAULT_MEMORY_TRACE")}))' > "${run_dir}/environment.json"
sha256sum "${run_dir}"/*.py "${run_dir}"/*.sh "${run_dir}"/*.gdb "${run_dir}/command.json" \
  "${run_dir}/environment.json" "${binary}" "${debug_binary}" "${icd}" "$(command -v gdb)" \
  "$(command -v python3)" > "${run_dir}/inputs.sha256"
step=capture
(
  cd "${client_root}"
  "${command[@]}"
) > "${run_dir}/gdb.log" 2>&1
step=validate
python3 "${run_dir}/vulkan_fault_snapshot.py" --run-dir "${run_dir}" > "${run_dir}/validation.log" 2>&1
sha256sum "${run_dir}/fault-capture.json" "${run_dir}/fault-analysis.json" >> "${run_dir}/inputs.sha256"
if [[ -f "${run_dir}/shader-container.bin" ]]; then
  sha256sum "${run_dir}/shader-container.bin" >> "${run_dir}/inputs.sha256"
fi
if [[ -f "${run_dir}/graphics-memory-trace.txt" ]]; then
  sha256sum "${run_dir}/graphics-memory-trace.txt" >> "${run_dir}/inputs.sha256"
fi
step=captured
