#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

client_root="${CARLA_COOKED_CLIENT_ROOT:-/artifacts/carla/cooked-client-full/CarlaUnreal}"
client_binary="${CARLA_COOKED_CLIENT_BINARY:-${client_root}/Binaries/LinuxArm64/CarlaUnreal}"
# Town01_Opt carries the passing RGB/LiDAR, Traffic Manager and walker evidence. On
# Town10HD_Opt the cooked client exits ~0.5s after "New episode ... started": the probe
# completes the version handshake and then fails in get_world(), reproduced on both the
# under-cooked and the full-cook client. Override CARLA_COOKED_CLIENT_MAP to retest it.
client_map="${CARLA_COOKED_CLIENT_MAP:-/Game/Carla/Maps/Town01_Opt}"
rpc_port="${CARLA_RUNTIME_PORT:-2000}"
ticks="${CARLA_RUNTIME_TICKS:-20}"
timeout_seconds="${CARLA_RUNTIME_TIMEOUT:-30}"
total_timeout="${CARLA_RUNTIME_TOTAL_TIMEOUT:-900}"
startup_timeout="${CARLA_CLIENT_STARTUP_TIMEOUT:-60}"
apt_timeout="${CARLA_LAVAPIPE_APT_TIMEOUT:-180}"
mode="${CARLA_RUNTIME_MODE:-sensors}"
render_profile="${CARLA_LAVAPIPE_RENDER_PROFILE:-default}"
container_image="${CARLA_LAVAPIPE_IMAGE:-ubuntu:24.04}"
container_name="carla-lavapipe-gate-$$"
build_container="${CARLA_BUILD_CONTAINER:-carla-build-session}"
provenance="${CARLA_RUNTIME_PROVENANCE:-/artifacts/carla/cooked-server-full/runtime-provenance.json}"
wheel="${CARLA_RUNTIME_WHEEL:-/artifacts/carla/cmake-arm64/PythonAPI/dist/carla-0.10.0-cp310-cp310-linux_aarch64.whl}"
vk_icd="/usr/share/vulkan/icd.d/lvp_icd.json"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/arm64-renderer-scope.sh"
[[ "${artifact_dir}" == /artifacts/carla ]] || {
  echo "CARLA_ARTIFACT_DIR must remain /artifacts/carla for the build-container probe" >&2
  exit 64
}

# Run on the DGX Spark host. The build container owns the shared CARLA artifacts
# and executes the Python client; the separate container supplies Lavapipe.

for value in "${rpc_port}" "${ticks}" "${timeout_seconds}" "${total_timeout}" "${startup_timeout}" "${apt_timeout}"; do
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || {
    echo "Runtime limits must be positive integers" >&2
    exit 64
  }
done
(( rpc_port <= 65535 && ticks <= 10000 && timeout_seconds <= 120 && startup_timeout <= 600 && apt_timeout <= 600 )) || {
  echo "Runtime limits are out of range" >&2
  exit 64
}
[[ "${mode}" == rpc || "${mode}" == sensors || "${mode}" == actors ]] || {
  echo "This wrapper only supports CARLA_RUNTIME_MODE=rpc, sensors or actors" >&2
  exit 64
}
[[ "${render_profile}" == default || "${render_profile}" == no-pso ]] || {
  echo "CARLA_LAVAPIPE_RENDER_PROFILE must be default or no-pso" >&2
  exit 64
}

required_input_check() {
  docker exec "${build_container}" test -e "$1"
}
command -v docker >/dev/null 2>&1 || {
  echo "docker is required on the host" >&2
  exit 2
}
docker inspect "${build_container}" >/dev/null 2>&1 || {
  echo "Native ARM64 build container is not running: ${build_container}" >&2
  exit 2
}
required_input_check "${client_binary}"
required_input_check "${client_root}/AssetRegistry.bin"
required_input_check "${client_root}/Plugins/Carla/Content/PostProcessingMaterials"
required_input_check "${client_root}/../Engine/OverrideGlobalShaderCache-VULKAN_SM6.bin"
required_input_check "${provenance}"
required_input_check "${wheel}"

shared_artifact_host="$(docker inspect "${build_container}" --format \
  '{{range .Mounts}}{{if eq .Destination "/artifacts/carla"}}{{.Source}}{{end}}{{end}}')"
[[ -n "${shared_artifact_host}" ]] || {
  echo "Build container does not mount /artifacts/carla" >&2
  exit 2
}

run_dir="$(docker exec "${build_container}" mktemp -d "${artifact_dir}/lavapipe-sensor-gate-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
docker exec "${build_container}" chmod 0755 "${run_dir}"
server_log="${run_dir}/server.log"
cleanup_started=0

write_server_diagnostics() {
  local diagnostics_file="${run_dir}/server-diagnostics.txt"
  {
    printf "# Lavapipe client/server diagnostics\n"
    printf "# Source log: %s\n" "${server_log}"
    for pattern in \
      "Couldn't find file for package" \
      "Found 0 dependent packages" \
      "Ensure condition failed" \
      "Expected source texture to be in VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL" \
      "has no SM assigned to the ISM" \
      "Fatal error!" \
      "Unhandled Exception:" \
      "SIGSEGV" \
      "RequestExitWithStatus"; do
      printf "\n## %s\n" "${pattern}"
      docker exec "${build_container}" grep -aFn "${pattern}" "${server_log}" || true
    done
  } | docker exec -i "${build_container}" sh -c 'cat > "$0"' "${diagnostics_file}"
}

cleanup() {
  local code=$?
  (( cleanup_started++ )) || true
  trap - EXIT INT TERM
  set +e
  local pid
  pid="$(docker exec "${container_name}" pgrep -n CarlaUnreal 2>/dev/null || true)"
  if [[ -n "${pid}" ]]; then
    docker exec "${container_name}" kill -TERM "${pid}" >/dev/null 2>&1 || true
    sleep 3
    pid="$(docker exec "${container_name}" pgrep -n CarlaUnreal 2>/dev/null || true)"
    [[ -z "${pid}" ]] || docker exec "${container_name}" kill -KILL "${pid}" >/dev/null 2>&1 || true
  fi
  write_server_diagnostics
  docker rm -f "${container_name}" >/dev/null 2>&1 || true
  exit "${code}"
}
trap cleanup EXIT INT TERM

docker exec "${build_container}" sh -c \
  'umask 022; printf "%s\n" "$@" > "$0"' \
  "${run_dir}/inputs.txt" \
  "client_root=${client_root}" \
  "client_binary=${client_binary}" \
  "client_map=${client_map}" \
  "rpc_port=${rpc_port}" \
  "ticks=${ticks}" \
  "timeout_seconds=${timeout_seconds}" \
  "total_timeout=${total_timeout}" \
  "startup_timeout=${startup_timeout}" \
  "apt_timeout=${apt_timeout}" \
  "mode=${mode}" \
  "render_profile=${render_profile}" \
  "container_image=${container_image}" \
  "build_container=${build_container}" \
  "provenance=${provenance}" \
  "wheel=${wheel}"

docker run -d \
  --name "${container_name}" \
  --platform linux/arm64 \
  --network bridge \
  -v "${shared_artifact_host}:/artifacts/carla:rw" \
  "${container_image}" sleep infinity
container_id="$(docker inspect "${container_name}" --format '{{.Id}}')"
docker exec "${build_container}" sh -c \
  'printf "%s\n" "$1" > "$0"' "${run_dir}/container-id.txt" "${container_id}"

for _ in $(seq 1 30); do
  container_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${container_name}")"
  [[ -n "${container_ip}" ]] && break
  sleep 1
done
[[ -n "${container_ip}" ]] || {
  echo "Could not determine Lavapipe container IP" >&2
  exit 2
}
docker exec "${build_container}" sh -c 'printf "%s\n" "$1" > "$0"' \
  "${run_dir}/container-ip.txt" "${container_ip}"

if ! docker exec "${container_name}" test -f "${vk_icd}"; then
  timeout --signal=TERM --kill-after=10 "${apt_timeout}" \
    docker exec "${container_name}" apt-get update -qq
  timeout --signal=TERM --kill-after=10 "${apt_timeout}" \
    docker exec "${container_name}" apt-get install -y --no-install-recommends \
    mesa-vulkan-drivers procps
fi
docker exec "${container_name}" test -f "${vk_icd}"
docker exec "${container_name}" sh -c 'command -v pgrep >/dev/null'
docker exec "${container_name}" test -x "${client_binary}"

client_command=(
  timeout --signal=INT --kill-after=30 $((total_timeout + startup_timeout + 120))
  "${client_binary}"
  "${client_map}"
  -carla-rpc-port="${rpc_port}"
  -vulkan
  -sm6
  -RenderOffScreen
  -no-rendering
  -quality-level=Low
  -AllowCPUDevices
  -SkipVulkanProfileCheck
  -nosound
  -NoSplash
  -noscript
  -ini:Engine:[/Script/Engine.RendererSettings]:r.Nanite.ProjectEnabled=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.Nanite.ForceEnableMeshes=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.Shadow.Virtual.Enable=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.VolumetricCloud=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.RayTracing=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.Lumen.TraceMeshSDFs=0
  -ini:Engine:[/Script/Engine.RendererSettings]:r.AllowOcclusionQueries=0
)
# Same shared scope the cook used. The cooked client has no shader permutations for the
# features in that list, and r.VirtualTextures/r.RayTracing are ECVF_ReadOnly, so leaving
# them enabled here aborts in FMaterial::GetShaderMap after map load.
while IFS= read -r renderer_flag; do
  client_command+=("${renderer_flag}")
done < <(carla_renderer_systemsettings_flags)
if [[ "${render_profile}" == no-pso ]]; then
  # Town10 can spend more than the default render-thread watchdog budget compiling
  # Lavapipe PSOs after episode start. This is an explicit diagnostic profile; the
  # default path remains unchanged until the effect is verified.
  client_command+=(
    -ini:Engine:[SystemSettings]:r.PSOPrecaching=0
    -ini:Engine:[SystemSettings]:r.Vulkan.AllowPSOPrecaching=0
    -ini:Engine:[SystemSettings]:r.AsyncPipelineCompile=0
    -ini:Engine:[SystemSettings]:r.Vulkan.RHIThread=0
    -ini:Engine:[ConsoleVariables]:g.TimeoutForBlockOnRenderFence=300000
  )
fi
client_command_json="$(mktemp /tmp/carla-client-command.XXXXXX)"
export CARLA_CLIENT_COMMAND_JSON="${client_command_json}"
# Pass the array through argv. An @Q expansion without a subscript yields element 0
# only, so every previously archived client-command.json recorded just ["timeout"]
# instead of the launch arguments that actually produced the evidence.
python3 - "${client_command[@]}" <<'PY'
import json
import os
import sys
path = os.environ["CARLA_CLIENT_COMMAND_JSON"]
with open(path, "w") as output:
    print(json.dumps(sys.argv[1:]), file=output)
PY
docker exec -i "${build_container}" sh -c 'cat > "$0"' \
  "${run_dir}/client-command.json" < "${client_command_json}"
rm -f "${client_command_json}"

docker exec \
  -e VK_ICD_FILENAMES="${vk_icd}" \
  -w "${client_root}" \
  "${container_name}" \
  bash -c 'exec "$@"' bash "${client_command[@]}" \
  | docker exec -i "${build_container}" sh -c \
      'cat > "$0"' "${server_log}" &
client_launcher_pid=$!

step=startup
for elapsed in $(seq 0 1 "${startup_timeout}"); do
  if ! kill -0 "${client_launcher_pid}" 2>/dev/null; then
    wait "${client_launcher_pid}" || true
    break
  fi
  if docker exec "${build_container}" grep -aFq \
    "New episode '${client_map##*/}' started" "${server_log}" 2>/dev/null; then
    step=ready
  break
  fi
  sleep 1
done

[[ "${step}" == ready ]] || {
  echo "CARLA client did not reach the startup marker within ${startup_timeout}s" >&2
  docker exec "${build_container}" tail -n 80 "${server_log}" >&2 || true
  exit 3
}

client_pid="$(docker exec "${container_name}" pgrep -n CarlaUnreal || true)"
[[ -n "${client_pid}" ]] || {
  echo "CARLA client process disappeared during startup" >&2
  exit 3
}
docker exec "${container_name}" grep -aFq "libvulkan_lvp.so" "/proc/${client_pid}/maps"
for marker in \
  "DeviceName: llvmpipe" \
  "Initialized CarlaServer: Ports(rpc=${rpc_port}, streaming=$((rpc_port + 1)), secondary=$((rpc_port + 2)))" \
  "New episode '${client_map##*/}' started"; do
  docker exec "${build_container}" grep -aFq "${marker}" "${server_log}" || {
    echo "Required Lavapipe startup marker is missing: ${marker}" >&2
    exit 3
  }
done
for forbidden in "Fatal error!" "Unhandled Exception:" "SIGSEGV" "Assertion failed:"; do
  if docker exec "${build_container}" grep -aFq "${forbidden}" "${server_log}"; then
    echo "Forbidden Lavapipe startup marker is present: ${forbidden}" >&2
    exit 3
  fi
done

step=sensor-gate
code=0
docker exec \
  -e CARLA_RUNTIME_HOST="${container_ip}" \
  -e CARLA_RUNTIME_MODE="${mode}" \
  -e CARLA_RUNTIME_TICKS="${ticks}" \
  -e CARLA_RUNTIME_PORT="${rpc_port}" \
  -e CARLA_RUNTIME_TIMEOUT="${timeout_seconds}" \
  -e CARLA_RUNTIME_TOTAL_TIMEOUT="${total_timeout}" \
  -e CARLA_ALLOW_WORLD_MUTATION=1 \
  -e CARLA_RUNTIME_PROVENANCE="${provenance}" \
  -e CARLA_RUNTIME_WHEEL="${wheel}" \
  -e CARLA_ARTIFACT_DIR="${artifact_dir}" \
  "${build_container}" \
  bash /opt/my-ad/scripts/carla/probe-arm64-runtime.sh || code=$?
[[ "${code}" -eq 0 ]] || {
  echo "${mode} gate failed; client log: ${server_log}" >&2
  exit "${code}"
}

step=complete
printf "PASS lavapipe-%s-gate artifacts=%s endpoint=%s:%s\n" \
  "${mode}" "${run_dir}" "${container_ip}" "${rpc_port}"
