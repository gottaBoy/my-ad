#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

mode="${CARLA_RUNTIME_MODE:-rpc}"
render_profile="${CARLA_GB10_RENDER_PROFILE:-default}"
port="${CARLA_RUNTIME_PORT:-20220}"
ticks="${CARLA_RUNTIME_TICKS:-20}"
startup_seconds="${CARLA_CLIENT_STARTUP_TIMEOUT:-120}"
gate_seconds="${CARLA_RUNTIME_TOTAL_TIMEOUT:-180}"
rpc_seconds="${CARLA_RUNTIME_TIMEOUT:-20}"
shader_diagnostics="${CARLA_RUNTIME_SHADER_DIAGNOSTICS:-0}"
serialize_compute="${CARLA_GB10_SERIALIZE_COMPUTE_PIPELINES:-0}"
pipeline_history="${CARLA_RUNTIME_PIPELINE_HISTORY:-0}"
pipeline_history_target="${CARLA_RUNTIME_PIPELINE_HISTORY_TARGET:-main_0000142c_a6b37050}"
serialize_graphics="${CARLA_GB10_SERIALIZE_GRAPHICS_PIPELINES:-0}"
serialize_mixed="${CARLA_GB10_SERIALIZE_MIXED_PIPELINES:-0}"
serialize_driver_calls="${CARLA_GB10_SERIALIZE_DRIVER_CALLS:-0}"
graphics_cache_snapshot="${CARLA_RUNTIME_GRAPHICS_CACHE_SNAPSHOT:-0}"
cache_lifecycle="${CARLA_RUNTIME_CACHE_LIFECYCLE:-0}"
null_pipeline_cache="${CARLA_RUNTIME_NULL_PIPELINE_CACHE:-0}"
validation="${CARLA_RUNTIME_VULKAN_VALIDATION:-0}"
validation_dir="${CARLA_VALIDATION_LAYER_DIR:-/opt/vulkan-validation-layer}"
debug_utils="${CARLA_RUNTIME_VULKAN_DEBUG_UTILS:-0}"
validation_stack_trace="${CARLA_RUNTIME_VULKAN_VALIDATION_STACK_TRACE:-}"
client_root="${CARLA_COOKED_CLIENT_ROOT:-/artifacts/carla/cooked-client-full/CarlaUnreal}"
client_log="${CARLA_RUNTIME_CLIENT_LOG:-${client_root}/Saved/Logs/CarlaUnreal.log}"
binary="${CARLA_RUNTIME_BINARY:-${client_root}/Binaries/LinuxArm64/CarlaUnreal}"
wheel="${CARLA_RUNTIME_WHEEL:-/artifacts/carla/cmake-arm64/PythonAPI/dist/carla-0.10.0-cp310-cp310-linux_aarch64.whl}"
provenance="${CARLA_RUNTIME_PROVENANCE:-/artifacts/carla/cooked-server-full/runtime-provenance.json}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
icd="${CARLA_GB10_ICD:-/etc/vulkan/icd.d/nvidia_icd.json}"
map="/Game/Carla/Maps/Town10HD_Opt"
source "${script_dir}/arm64-renderer-scope.sh"

[[ "${mode}" == rpc || "${mode}" == sensors || "${mode}" == actors ]] || {
  echo "CARLA_RUNTIME_MODE must be rpc, sensors or actors" >&2; exit 64;
}
[[ "${render_profile}" == default || "${render_profile}" == no-pso || "${render_profile}" == serial-translate ]] || {
  echo "CARLA_GB10_RENDER_PROFILE must be default, no-pso or serial-translate" >&2; exit 64;
}
[[ "${shader_diagnostics}" == 0 || "${shader_diagnostics}" == 1 ]] || {
  echo "CARLA_RUNTIME_SHADER_DIAGNOSTICS must be 0 or 1" >&2; exit 64;
}
[[ "${serialize_compute}" == 0 || "${serialize_compute}" == 1 ]] || {
  echo "CARLA_GB10_SERIALIZE_COMPUTE_PIPELINES must be 0 or 1" >&2; exit 64;
}
[[ "${pipeline_history}" == 0 || "${pipeline_history}" == 1 ]] || {
  echo "CARLA_RUNTIME_PIPELINE_HISTORY must be 0 or 1" >&2; exit 64;
}
[[ "${pipeline_history_target}" == checkpoint || "${pipeline_history_target}" =~ ^[A-Za-z_][A-Za-z0-9_]{0,63}(,[A-Za-z_][A-Za-z0-9_]{0,63})?$ ]] || {
  echo "CARLA_RUNTIME_PIPELINE_HISTORY_TARGET must be checkpoint or one/two comma-separated shader entry names" >&2; exit 64;
}
[[ "${serialize_graphics}" == 0 || "${serialize_graphics}" == 1 ]] || {
  echo "CARLA_GB10_SERIALIZE_GRAPHICS_PIPELINES must be 0 or 1" >&2; exit 64;
}
[[ "${serialize_mixed}" == 0 || "${serialize_mixed}" == 1 ]] || {
  echo "CARLA_GB10_SERIALIZE_MIXED_PIPELINES must be 0 or 1" >&2; exit 64;
}
[[ "${graphics_cache_snapshot}" == 0 || "${graphics_cache_snapshot}" == 1 ]] || {
  echo "CARLA_RUNTIME_GRAPHICS_CACHE_SNAPSHOT must be 0 or 1" >&2; exit 64;
}
[[ "${graphics_cache_snapshot}" == 0 || "${serialize_mixed}" == 1 ]] || {
  echo "Graphics cache capture requires mixed pipeline serialization" >&2; exit 64;
}
[[ "${graphics_cache_snapshot}" == 0 || "${pipeline_history}" == 1 ]] || {
  echo "Graphics cache capture requires pipeline history" >&2; exit 64;
}
[[ "${cache_lifecycle}" == 0 || "${cache_lifecycle}" == 1 ]] || {
  echo "CARLA_RUNTIME_CACHE_LIFECYCLE must be 0 or 1" >&2; exit 64;
}
[[ "${cache_lifecycle}" == 0 || ( "${serialize_mixed}" == 1 && "${pipeline_history}" == 1 ) ]] || {
  echo "Cache lifecycle capture requires mixed serialization and pipeline history" >&2; exit 64;
}
[[ "${null_pipeline_cache}" == 0 || "${null_pipeline_cache}" == 1 ]] || {
  echo "CARLA_RUNTIME_NULL_PIPELINE_CACHE must be 0 or 1" >&2; exit 64;
}
[[ "${null_pipeline_cache}" == 0 || ( "${serialize_mixed}" == 1 && "${pipeline_history}" == 1
   && "${cache_lifecycle}" == 1 ) ]] || {
  echo "Null pipeline cache diagnostic requires mixed serialization, history and lifecycle" >&2; exit 64;
}
[[ "${validation}" == 0 || "${validation}" == 1 ]] || {
  echo "CARLA_RUNTIME_VULKAN_VALIDATION must be 0 or 1" >&2; exit 64;
}
[[ "${serialize_driver_calls}" == 0 || "${serialize_driver_calls}" == 1 ]] || {
  echo "CARLA_GB10_SERIALIZE_DRIVER_CALLS must be 0 or 1" >&2; exit 64;
}
[[ "${debug_utils}" == 0 || "${debug_utils}" == 1 ]] || {
  echo "CARLA_RUNTIME_VULKAN_DEBUG_UTILS must be 0 or 1" >&2; exit 64;
}
[[ -z "${validation_stack_trace}" || "${validation_stack_trace}" =~ ^[A-Za-z0-9-]{1,32}$ ]] || {
  echo "CARLA_RUNTIME_VULKAN_VALIDATION_STACK_TRACE must be empty or a short VUID substring" >&2; exit 64;
}
for value in "${port}" "${ticks}" "${startup_seconds}" "${gate_seconds}" "${rpc_seconds}"; do
  [[ "${value}" =~ ^[1-9][0-9]*$ && ${#value} -le 6 ]] || {
    echo "GB10 runtime limits must be positive integers" >&2; exit 64;
  }
done
(( port >= 1024 && port <= 65532 && ticks <= 1000 && startup_seconds <= 300
   && gate_seconds <= 600 && rpc_seconds <= 120 )) || {
  echo "GB10 runtime limits are out of range" >&2; exit 64;
}
[[ "${mode}" == rpc || "${ticks}" -ge 2 ]] || { echo "Data-plane validation requires at least two ticks" >&2; exit 64; }
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || { echo "Native ARM64 GPU Docker required" >&2; exit 2; }
for path in "${binary}" "${wheel}" "${provenance}" "${icd}" \
  "${client_root}/AssetRegistry.bin" "${client_root}/Content/Carla/Maps/Town10HD_Opt.umap" \
  "${client_root}/Content/Carla/Maps/OpenDrive/Town10HD_Opt.xodr" \
  "${client_root}/Content/Carla/Maps/Nav/Town10HD_Opt.bin" \
  "${client_root}/../Engine/OverrideGlobalShaderCache-VULKAN_SM6.bin"; do
  [[ -f "${path}" ]] || { echo "Required GB10 runtime input is missing: ${path}" >&2; exit 2; }
done
[[ -x "${binary}" ]] || { echo "Staged client is not executable" >&2; exit 2; }
if [[ "${validation}" == 1 ]]; then
  for path in "${validation_dir}/VkLayer_khronos_validation.json" \
    "${validation_dir}/libVkLayer_khronos_validation.so" \
    "${validation_dir}/libVkLayer_utils.so"; do
    [[ -f "${path}" ]] || {
      echo "Requested Vulkan validation layer is missing: ${path}" >&2
      echo "Run scripts/carla/fetch-vulkan-validation-layer.sh first" >&2
      exit 2
    }
  done
fi
command -v vulkaninfo >/dev/null
command -v pgrep >/dev/null

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

run_dir="$(mktemp -d "${artifact_dir}/town10-gb10-${mode}-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
server_pid=""
step=preflight
finish() {
  local code=$? server_code=0 status=FAIL
  trap - EXIT INT TERM
  set +e
  if [[ -n "${server_pid}" ]]; then
    local process_group
    process_group="$(ps -o pgid= -p "${server_pid}" | tr -d '[:space:]')"
    if [[ "${process_group}" == "${server_pid}" ]]; then
      kill -TERM -- "-${server_pid}" 2>/dev/null
    else
      kill -TERM "${server_pid}" 2>/dev/null
    fi
    for _ in $(seq 1 5); do
      kill -0 "${server_pid}" 2>/dev/null || break
      sleep 1
    done
    if [[ "${process_group}" == "${server_pid}" ]]; then
      kill -KILL -- "-${server_pid}" 2>/dev/null
    else
      kill -0 "${server_pid}" 2>/dev/null && kill -KILL "${server_pid}" 2>/dev/null
    fi
    wait "${server_pid}"
    server_code=$?
  fi
  [[ "${code}" == 0 && "${step}" == complete ]] && status=PASS
  printf "%s\n" "${server_code}" > "${run_dir}/server-stop-code.txt"
  # UE writes the reason for an early abort (for example a failed
  # vkCreateDevice) only to its own log file; stdout can lose the tail when the
  # process exits before flushing.
  if [[ -f "${client_log}" ]]; then
    cp -a "${client_log}" "${run_dir}/client-ue.log" 2>/dev/null || true
  fi
  client_abort=none
  if [[ -f "${run_dir}/client-ue.log" ]]; then
    if grep -qa 'Cannot create a Vulkan device' "${run_dir}/client-ue.log"; then
      client_abort=device-creation
    elif grep -qa 'libnvidia-glvkspirv' "${run_dir}/client-ue.log"; then
      client_abort=shader-compiler-sigsegv
    elif grep -qa 'libnvidia-' "${run_dir}/client-ue.log"; then
      client_abort=nvidia-driver-sigsegv
    fi
  fi
  grep -aEn 'Ensure condition failed|Fatal error!|Unhandled Exception|Assertion failed|SIGSEGV|has no SM assigned|Cannot create a Vulkan device|Vulkan device creation failed' \
    "${run_dir}/server.log" "${run_dir}/client-ue.log" > "${run_dir}/server-diagnostics.txt" 2>/dev/null
  if [[ "${serialize_mixed}" == 1 && "${pipeline_history}" == 1 ]] &&
    compgen -G "${run_dir}/driver-graphics-*.enter.txt" >/dev/null &&
    compgen -G "${run_dir}/driver-compute-*.enter.txt" >/dev/null; then
    local analysis_code=0
    python3 "${script_dir}/analyze_vulkan_driver_entries.py" \
      --run-dir "${run_dir}" --output "${run_dir}/driver-entry-analysis.json" \
      > "${run_dir}/driver-entry-analysis.log" 2>&1 || analysis_code=$?
    printf '%s\n' "${analysis_code}" > "${run_dir}/driver-entry-analysis-code.txt"
  fi
  if [[ "${cache_lifecycle}" == 1 && -f "${run_dir}/cache-lifecycle.txt" ]]; then
    local lifecycle_code=0
    python3 "${script_dir}/analyze_vulkan_cache_lifecycle.py" \
      --run-dir "${run_dir}" --output "${run_dir}/cache-lifecycle-analysis.json" \
      > "${run_dir}/cache-lifecycle-analysis.log" 2>&1 || lifecycle_code=$?
    printf '%s\n' "${lifecycle_code}" > "${run_dir}/cache-lifecycle-analysis-code.txt"
  fi
  printf "# Town10 GB10 Runtime\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Mode: %s\n- Render profile: %s\n- Server stop code: %s\n- Vulkan validation: %s\n- Vulkan debug utils: %s\n- Client abort: %s\n- Scope: staged native ARM64 CARLA on identified NVIDIA GB10, expected Town10 RPC world and requested endpoint gate\n- Excluded: graceful server shutdown, clean image, throughput, exhaustive visual correctness, ROS/Autoware integration\n" \
    "${status}" "${step}" "${code}" "${mode}" "${render_profile}" "${server_code}" "${validation}" "${debug_utils}" "${client_abort}" > "${run_dir}/decision.md"
  printf "%s town10-gb10-%s artifacts=%s step=%s exit=%s\n" "${status}" "${mode}" "${run_dir}" "${step}" "${code}"
  exit "${code}"
}
trap finish EXIT

export VK_ICD_FILENAMES="${icd}"
export XDG_RUNTIME_DIR="${run_dir}/xdg"
mkdir -m 0700 "${XDG_RUNTIME_DIR}"
if [[ "${validation}" == 1 ]]; then
  export VK_LAYER_PATH="${validation_dir}"
  export LD_LIBRARY_PATH="${validation_dir}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
  python3 - "${validation_dir}" "${run_dir}/vulkan-validation.json" <<'PY'
import hashlib
import json
import pathlib
import sys

layer_dir, output = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
with open(layer_dir / "VkLayer_khronos_validation.json", encoding="utf-8") as stream:
    layer = json.load(stream)["layer"]
record = {
    "mode": "validation-layer",
    "layer_dir": str(layer_dir),
    "layer_name": layer["name"],
    "api_version": layer["api_version"],
    "enablement": "VK_LAYER_PATH plus -vulkanvalidation=1",
    "files": {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(layer_dir.iterdir())
        if path.is_file()
    },
    "scope": "Opt-in API usage validation only; the layer changes timing and is not a product configuration",
}
with open(output, "w", encoding="utf-8") as stream:
    json.dump(record, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
fi
# vulkaninfo intermittently fails to create a device on GB10 when the GPU is
# shared with other containers, which used to abort the gate with a bare exit 1
# for a reason unrelated to the product run. Retry before declaring a failure.
vulkaninfo_attempts="${CARLA_GB10_VULKANINFO_ATTEMPTS:-3}"
[[ "${vulkaninfo_attempts}" =~ ^[1-9][0-9]*$ && ${#vulkaninfo_attempts} -le 2 ]] || {
  echo "CARLA_GB10_VULKANINFO_ATTEMPTS must be a small positive integer" >&2; exit 64;
}
vulkaninfo_ok=0
for attempt in $(seq 1 "${vulkaninfo_attempts}"); do
  if timeout --signal=TERM --kill-after=5 30 vulkaninfo --summary \
    > "${run_dir}/vulkaninfo.log" 2>&1; then
    vulkaninfo_ok=1
    break
  fi
  echo "vulkaninfo attempt ${attempt} failed" >&2
  sleep 2
done
if [[ "${vulkaninfo_ok}" != 1 ]]; then
  echo "vulkaninfo device probe failed after ${vulkaninfo_attempts} attempts" >&2
  exit 3
fi
grep -Eq '^[[:space:]]*deviceName[[:space:]]*=[[:space:]]*NVIDIA GB10[[:space:]]*$' "${run_dir}/vulkaninfo.log" || {
  echo "Required NVIDIA GB10 backend was not found" >&2; exit 3;
}
python3 - "${binary}" <<'PY' > "${run_dir}/server-architecture.json"
import json
import struct
import sys
with open(sys.argv[1], "rb") as stream:
    header = stream.read(64)
if len(header) != 64 or header[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", header, 18)[0] != 183:
    raise SystemExit("server must be a little-endian ELF64 AArch64 binary")
print(json.dumps({"file": sys.argv[1], "elf_class": 64, "machine": "AArch64"}))
PY
python3 -m pip install --no-index --no-deps --force-reinstall "${wheel}" > "${run_dir}/client-install.log" 2>&1
python3 -c 'import carla,numpy; print(carla.__file__); print(numpy.__version__)' > "${run_dir}/client-environment.txt"
cp -a "${BASH_SOURCE[0]}" "${script_dir}/wait_carla_world.py" \
  "${script_dir}/check_carla_runtime.py" "${script_dir}/stage_report.py" \
  "${script_dir}/arm64-renderer-scope.sh" "${run_dir}/"
if [[ "${cache_lifecycle}" == 1 ]]; then
  cp -a "${script_dir}/analyze_vulkan_cache_lifecycle.py" \
    "${script_dir}/analyze_vulkan_driver_entries.py" "${run_dir}/"
fi
sha256sum "${binary}" "${wheel}" "${provenance}" "${icd}" "${run_dir}"/*.py "${run_dir}"/*.sh \
  > "${run_dir}/inputs.sha256"
mapfile -t renderer_flags < <(carla_renderer_systemsettings_flags)
command=(
  timeout --signal=INT --kill-after=10 "$((startup_seconds + gate_seconds + 30))"
  "${binary}" "${map}" -carla-rpc-port="${port}" -vulkan -sm6
  -RenderOffScreen -no-rendering -quality-level=Low -nosound -NoSplash -noscript
  -ini:Engine:[/Script/Engine.RendererSettings]:r.Nanite.ProjectEnabled=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.Nanite.ForceEnableMeshes=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.Shadow.Virtual.Enable=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.VolumetricCloud=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.RayTracing=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.Lumen.TraceMeshSDFs=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.AllowOcclusionQueries=0
  "${renderer_flags[@]}"
)
if [[ "${render_profile}" == no-pso || "${render_profile}" == serial-translate ]]; then
  command+=(
    -ini:Engine:[SystemSettings]:r.PSOPrecaching=0
    -ini:Engine:[SystemSettings]:r.Vulkan.AllowPSOPrecaching=0
    -ini:Engine:[SystemSettings]:r.AsyncPipelineCompile=0
    -ini:Engine:[SystemSettings]:r.Vulkan.RHIThread=0
  )
fi
if [[ "${render_profile}" == serial-translate ]]; then
  command+=(-ini:Engine:[SystemSettings]:r.RHICmd.ParallelTranslate.Enable=0)
fi
if [[ "${shader_diagnostics}" == 1 ]]; then
  command+=("-CarlaVulkanShaderDiagnostics=${run_dir}/shader-diagnostics")
fi
if [[ "${serialize_compute}" == 1 ]]; then
  command+=(-CarlaVulkanSerializeComputePipelineCreation)
fi
if [[ "${pipeline_history}" == 1 ]]; then
  command+=(
    "-CarlaVulkanPipelineHistory=${run_dir}"
    "-CarlaVulkanPipelineHistoryTarget=${pipeline_history_target}"
  )
fi
if [[ "${serialize_graphics}" == 1 ]]; then
  command+=(-CarlaVulkanSerializeGraphicsPipelineCreation)
fi
if [[ "${serialize_mixed}" == 1 ]]; then
  command+=(-CarlaVulkanSerializeMixedPipelineCreation)
fi
if [[ "${graphics_cache_snapshot}" == 1 ]]; then
  command+=(-CarlaVulkanGraphicsCacheSnapshot)
fi
if [[ "${cache_lifecycle}" == 1 ]]; then
  command+=(-CarlaVulkanCacheLifecycle)
fi
if [[ "${null_pipeline_cache}" == 1 ]]; then
  command+=(-CarlaVulkanSubmitNullPipelineCache)
fi
if [[ "${validation}" == 1 ]]; then
  command+=(-vulkanvalidation=1)
fi
if [[ "${serialize_driver_calls}" == 1 ]]; then
  command+=(-CarlaVulkanSerializeDriverCalls)
fi
if [[ "${debug_utils}" == 1 ]]; then
  command+=(-vulkandebugutils)
fi
if [[ -n "${validation_stack_trace}" ]]; then
  command+=("-CarlaVulkanValidationStackTrace=${validation_stack_trace}")
fi
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" > "${run_dir}/server-command.json"
step=startup
(
  cd "${client_root}"
  exec "${command[@]}"
) > "${run_dir}/server.log" 2>&1 &
server_pid=$!

timeout --signal=TERM --kill-after=5 "$((startup_seconds + 5))" \
  python3 "${script_dir}/wait_carla_world.py" --port "${port}" --map Town10HD_Opt \
  --seconds "${startup_seconds}" --server-pid "${server_pid}" --output "${run_dir}/readiness.json" \
  > "${run_dir}/ready.log" 2>&1
step=backend
pid="$(pgrep -P "${server_pid}" -n CarlaUnreal)"
[[ -n "${pid}" ]] || { echo "Owned CARLA process was not found" >&2; exit 3; }
readlink "/proc/${pid}/exe" > "${run_dir}/server-executable.txt"
[[ "$(readlink -f "/proc/${pid}/exe")" == "$(readlink -f "${binary}")" ]] || {
  echo "Running server executable differs from staged binary" >&2; exit 3;
}
grep -aE 'libGLX_nvidia|libnvidia-(glcore|gpucomp|glvkspirv)' "/proc/${pid}/maps" > "${run_dir}/loaded-driver.txt"
grep -aFq "DeviceName: NVIDIA GB10" "${run_dir}/server.log"
if grep -aEq 'Fatal error!|Unhandled Exception|Assertion failed|SIGSEGV' "${run_dir}/server.log"; then
  echo "Server has a fatal startup marker" >&2; exit 3;
fi
step="${mode}"
command=(
  timeout --signal=TERM --kill-after=5 "${gate_seconds}"
  python3 "${script_dir}/check_carla_runtime.py" --mode "${mode}" --host 127.0.0.1
  --port "${port}" --ticks "${ticks}" --timeout "${rpc_seconds}" --run-dir "${run_dir}/endpoint"
  --provenance "${provenance}" --allow-world-mutation
)
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" > "${run_dir}/client-command.json"
"${command[@]}" > "${run_dir}/client.log" 2>&1
sha256sum --check "${run_dir}/inputs.sha256" > "${run_dir}/input-validation.log" 2>&1
step=complete
