#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

client_root="${CARLA_COOKED_CLIENT_ROOT:-/artifacts/carla/cooked-client-full/CarlaUnreal}"
binary="${CARLA_UE_FAULT_BINARY:-${client_root}/Binaries/LinuxArm64/CarlaUnreal}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
port="${CARLA_UE_MEMORY_TRACE_PORT:-20232}"
timeout_seconds="${CARLA_UE_MEMORY_TRACE_TIMEOUT:-180}"
icd="${CARLA_GB10_ICD:-/etc/vulkan/icd.d/nvidia_icd.json}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/arm64-vulkan-diagnostic-scope.sh"

[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ && ${#timeout_seconds} -le 3 && "${timeout_seconds}" -le 300 ]] || {
  echo "CARLA_UE_MEMORY_TRACE_TIMEOUT must be 1..300" >&2; exit 64;
}
[[ "${port}" =~ ^[1-9][0-9]*$ && "${port}" -ge 1024 && "${port}" -le 65532 ]] || {
  echo "CARLA_UE_MEMORY_TRACE_PORT must be 1024..65532" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 && -x "${binary}" && -f "${icd}" ]] || {
  echo "Native ARM64 GPU Docker, runtime binary and NVIDIA ICD required" >&2; exit 2;
}
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

run_dir="$(mktemp -d "${artifact_dir}/ue-vulkan-memory-trace-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 && "${step}" == captured ]] && status=CAPTURED_MISMATCH
  printf "# UE Vulkan Memory Trace\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Execution: direct binary, no GDB\n- Scope: diagnostic mismatch trace only; no runtime/tick/sensor acceptance\n" \
    "${status}" "${step}" "${code}" > "${run_dir}/decision.md"
  printf "%s ue-vulkan-memory-trace artifacts=%s step=%s exit=%s\n" \
    "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

export VK_ICD_FILENAMES="${icd}"
export XDG_RUNTIME_DIR="${run_dir}/xdg"
mkdir -m 0700 "${XDG_RUNTIME_DIR}"
timeout --signal=TERM --kill-after=5 30 vulkaninfo --summary > "${run_dir}/vulkaninfo.log" 2>&1
grep -Eq '^[[:space:]]*deviceName[[:space:]]*=[[:space:]]*NVIDIA GB10[[:space:]]*$' "${run_dir}/vulkaninfo.log" || {
  echo "NVIDIA GB10 backend required" >&2; exit 3;
}

client_args=(/Game/Carla/Maps/Town10HD_Opt -carla-rpc-port="${port}"
  -CarlaVulkanGraphicsMemoryTrace="${run_dir}")
while IFS= read -r flag; do
  client_args+=("${flag}")
done < <(carla_vulkan_diagnostic_flags)

command=(
  timeout --signal=TERM --kill-after=5 "$((timeout_seconds + 30))"
  "${binary}" "${client_args[@]}"
)
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" > "${run_dir}/command.json"
python3 -c 'import json,os; print(json.dumps({key:os.environ.get(key) for key in ("PATH","LD_LIBRARY_PATH","VK_ICD_FILENAMES","CARLA_UE_FAULT_BINARY","CARLA_UE_MEMORY_TRACE_TIMEOUT")}))' \
  > "${run_dir}/environment.json"
sha256sum "${binary}" "${icd}" "${run_dir}/command.json" "${run_dir}/environment.json" \
  "${script_dir}/arm64-vulkan-diagnostic-scope.sh" "${script_dir}/arm64-renderer-scope.sh" \
  "${script_dir}/vulkan_graphics_memory_trace.py" > "${run_dir}/inputs.sha256"

step=run
set +e
(
  cd "${client_root}"
  "${command[@]}"
) > "${run_dir}/runtime.log" 2>&1
run_code=$?
set -e
printf "%s\n" "${run_code}" > "${run_dir}/runtime-exit-code.txt"

step=validate
if [[ ! -f "${run_dir}/graphics-memory-trace.txt" ]]; then
  echo "direct run ended without a memory mismatch trace" > "${run_dir}/validation.log"
  exit 3
fi
python3 "${script_dir}/vulkan_graphics_memory_trace.py" \
  --trace "${run_dir}/graphics-memory-trace.txt" \
  --output "${run_dir}/trace-analysis.json" > "${run_dir}/validation.log" 2>&1
sha256sum "${run_dir}/graphics-memory-trace.txt" "${run_dir}/trace-analysis.json" >> "${run_dir}/inputs.sha256"
step=captured
