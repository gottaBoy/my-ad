#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

timeout_seconds="${CARLA_COOKED_SERVER_TIMEOUT:-30}"
[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || {
  echo "CARLA_COOKED_SERVER_TIMEOUT must be positive" >&2
  exit 64
}
require_content="${CARLA_COOKED_SERVER_REQUIRE_CONTENT:-1}"
[[ "${require_content}" == 0 || "${require_content}" == 1 ]] || {
  echo "CARLA_COOKED_SERVER_REQUIRE_CONTENT must be 0 or 1" >&2
  exit 64
}
server_root="${CARLA_COOKED_SERVER_ROOT:-/artifacts/carla/cooked-server/CarlaUnreal}"
map_name="${CARLA_COOKED_SERVER_MAP:-/Game/Carla/Maps/OpenDriveMap}"
server_port="${CARLA_COOKED_SERVER_PORT:-7777}"
[[ "${map_name}" == /Game/* ]] || {
  echo "CARLA_COOKED_SERVER_MAP must be a /Game map path" >&2
  exit 64
}
[[ "${server_port}" =~ ^[1-9][0-9]*$ ]] || {
  echo "CARLA_COOKED_SERVER_PORT must be positive" >&2
  exit 64
}
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi

server="${server_root}/Binaries/LinuxArm64/CarlaUnrealServer"
project="${server_root}/CarlaUnreal.uproject"
asset_registry="${server_root}/AssetRegistry.bin"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/cooked-server-probe-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight

finish() {
  local code=$? status=BLOCKED
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 Cooked Dedicated Server Probe\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Probe timeout: %ss\n- Server package: %s\n- Map: %s\n- Port: %s\n- Require content: %s\n- Scope: cooked package startup, CarlaGameMode load, map play, and dedicated-server socket stability\n- Excluded: Vulkan rendering, CARLA client RPC, sensors, traffic/walker gameplay, and full-project cooked content\n" \
    "${status}" "${step}" "${code}" "${timeout_seconds}" "${server_root}" "${map_name}" "${server_port}" "${require_content}" \
    > "${run_dir}/decision.md"
  printf "%s cooked-server-probe artifacts=%s step=%s exit=%s\n" \
    "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

[[ -x "${server}" ]] || { echo "Cooked server binary is missing: ${server}" >&2; exit 2; }
[[ -f "${project}" ]] || { echo "Cooked project descriptor is missing: ${project}" >&2; exit 2; }
[[ -f "${asset_registry}" ]] || { echo "Cooked AssetRegistry.bin is missing: ${asset_registry}" >&2; exit 2; }

command=(
  timeout --signal=INT --kill-after=10 "${timeout_seconds}" "${server}"
  "${map_name}"
  -server
  -port="${server_port}"
  -nullrhi
  -nosound
  -unattended
  -NoSplash
  -notraceserver
  -stdout
  -FullStdOutLogOutput
  -log
)
printf "%q " "${command[@]}" > "${run_dir}/command.txt"
printf "\n" >> "${run_dir}/command.txt"
printf "%s\n" "${map_name}" > "${run_dir}/map.txt"
printf "%s\n" "${server_port}" > "${run_dir}/port.txt"
sha256sum "${server}" "${project}" "${asset_registry}" > "${run_dir}/package.sha256"

step=dedicated-server
ulimit -c 0
set +e
"${command[@]}" 2>&1 | tee "${run_dir}/server.log"
exit_code=${PIPESTATUS[0]}
set -e
printf "%s\n" "${exit_code}" > "${run_dir}/exit-code.txt"
step=log-validation

if [[ "${exit_code}" -ne 124 ]]; then
  echo "Cooked server did not remain stable until the probe timeout; exit ${exit_code}" >&2
  exit 3
fi

map_package="$(basename "${map_name}")"
required_markers=(
  "LogAssetRegistry: Premade AssetRegistry loaded"
  "LogLoad: Game class is 'CarlaGameMode_C'"
  "LogWorld: Bringing World ${map_name}.${map_package} up for play"
  "LogNet: Name:GameNetDriver Def:GameNetDriver"
  "IpNetDriver listening on port ${server_port}"
)
for marker in "${required_markers[@]}"; do
  if ! grep -aFq "${marker}" "${run_dir}/server.log"; then
    echo "Required cooked server marker is missing: ${marker}" >&2
    exit 3
  fi
done

rejected_markers=(
  "Assertion failed:"
  "Ensure condition failed:"
  "Invalid InputComponent class"
  "SIGSEGV"
  "Fatal error!"
  "Unhandled Exception:"
  "Critical error:"
  "failed to load because module"
  "ICU data directory was not discovered"
  "Failed to load file:"
  "Missing weather class!"
  "No OpenDrive file found for map"
  "The OpenDrive is empty"
  "The OpenDrive has not been loaded"
  "Couldn't find file for package /Game/Carla/Blueprints/Game/CarlaGameMode"
)
for marker in "${rejected_markers[@]}"; do
  if grep -aFq "${marker}" "${run_dir}/server.log"; then
    echo "Forbidden cooked server marker is present: ${marker}" >&2
    exit 3
  fi
done

missing_soft_references="$(grep -ac "Couldn't find file for package" "${run_dir}/server.log" || true)"
printf "%s\n" "${missing_soft_references}" > "${run_dir}/missing-soft-references.txt"
if [[ "${require_content}" == 1 && "${missing_soft_references}" -ne 0 ]]; then
  echo "Cooked server has ${missing_soft_references} missing package references" >&2
  exit 3
fi

step=complete
