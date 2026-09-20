#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

mode="${1:-self-test}"
case "${mode}" in
  self-test|static) ;;
  *) echo "Usage: debug-ue-meshbridge.sh [self-test|static]" >&2; exit 64 ;;
esac
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run in the native ARM64 carla-dev container" >&2
  exit 2
}
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
binary="${artifact_dir}/ue-meshbridge/project/Binaries/Linux/CarlaMeshBridge"
[[ -x "${binary}" ]] || { echo "Mesh bridge binary is missing" >&2; exit 2; }
run_dir="$(mktemp -d "${artifact_dir}/ue-meshbridge-debug-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
printf "debug_artifacts=%s\n" "${run_dir}"
if ! command -v gdb >/dev/null; then
  apt-get update > "${run_dir}/apt-update.log" 2>&1
  apt-get install -y --no-install-recommends gdb > "${run_dir}/apt-install.log" 2>&1
fi
ulimit -c 0
probe_argument=-self-test
if [[ "${mode}" == static ]]; then
  probe_argument="-input=${CARLA_UE_DIR:-/workspace/unreal-engine}/Engine/Content/FbxEditorAutomation/BlenderCube.fbx"
fi
command=(
  env -u LD_LIBRARY_PATH timeout --kill-after=10 180 gdb --batch
  -ex "set pagination off" -ex "set disable-randomization off"
  -ex run -ex "thread apply all bt" -ex "info sharedlibrary"
  --args "${binary}" "${probe_argument}" "-output=${run_dir}/raw.json"
  -unattended -stdout -notraceserver -traceautostart=0
)
printf "%q " "${command[@]}" > "${run_dir}/command.txt"
printf "\n" >> "${run_dir}/command.txt"
"${command[@]}" > "${run_dir}/gdb.log" 2>&1
tail -n 100 "${run_dir}/gdb.log"
printf "OBSERVED debugger session only; no stage PASS implied\n"
