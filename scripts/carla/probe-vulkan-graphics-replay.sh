#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

action="${CARLA_GRAPHICS_REPLAY_ACTION:-prepare}"
capture="${CARLA_GRAPHICS_CAPTURE_ROOT:-}"
device_state="${CARLA_GRAPHICS_DEVICE_STATE:-}"
stage="${CARLA_GRAPHICS_REPLAY_STAGE:-}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"

[[ "${action}" == prepare || "${action}" == run ]] || {
  echo "CARLA_GRAPHICS_REPLAY_ACTION must be prepare or run" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Native ARM64 Docker required" >&2; exit 2;
}

if [[ "${action}" == prepare ]]; then
  [[ "${capture}" == /* && -f "${capture}/driver-entry-analysis.json"
     && "${device_state}" == /* && -f "${device_state}" ]] || {
    echo "Valid absolute graphics capture and device snapshot required" >&2; exit 2;
  }
  command -v cc >/dev/null
  command -v spirv-val >/dev/null
  stage="$(mktemp -d "${artifact_dir}/vulkan-graphics-replay-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
  chmod 0755 "${stage}"
else
  [[ "${stage}" == "${artifact_dir}"/vulkan-graphics-replay-* && -d "${stage}"
     && ! -L "${stage}" ]] || {
    echo "A prepared graphics replay stage under CARLA_ARTIFACT_DIR is required" >&2; exit 64;
  }
fi

step=preflight
finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 && "${step}" == complete ]] && status=PASS
  printf "# Isolated GB10 Graphics Create\n\n- Action: %s\n- Status: %s\n- Step: %s\n- Exit: %s\n- Stage: %s\n- Boundary: isolated pipeline creation with a separately captured device configuration; not UE lifecycle or CARLA world/sensor acceptance\n" \
    "${action}" "${status}" "${step}" "${code}" "${stage}" \
    > "${stage}/${action}-decision.md"
  printf "%s graphics-replay-%s artifacts=%s step=%s exit=%s\n" \
    "${status}" "${action}" "${stage}" "${step}" "${code}"
}
trap finish EXIT

if [[ "${action}" == prepare ]]; then
  step=inputs
  cp -a "${device_state}" "${stage}/device-create.json"
  cp -a "${script_dir}/vulkan-graphics-replay.c" "${stage}/"
  cp -a "${script_dir}/prepare_vulkan_graphics_replay.py" \
    "${script_dir}/analyze_vulkan_driver_entries.py" \
    "${script_dir}/vulkan_device_snapshot.py" "${stage}/"
  python3 "${stage}/prepare_vulkan_graphics_replay.py" \
    --run-dir "${capture}" --header "${stage}/captured-graphics.h" \
    --report "${stage}/driver-input.json" > "${stage}/graphics-validation.log"
  python3 "${stage}/vulkan_device_snapshot.py" \
    --input "${stage}/device-create.json" \
    --header "${stage}/captured-device.h" > "${stage}/device-validation.log"
  python3 - "${capture}" "${stage}" <<'PY'
import json
import hashlib
from pathlib import Path
import shutil
import sys
root, stage = map(Path, sys.argv[1:])
call = json.loads((stage / "driver-input.json").read_text())["unreturned"][0]
for index, shader in enumerate(call["stages"]):
    target = stage / ("vertex.spv" if index == 0 else "fragment.spv")
    shutil.copyfile(root / shader["file"], target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != shader["sha256"]:
        raise SystemExit("Shader changed while preparing replay")
cache = root / Path(call["cache_data"]).name
shutil.copyfile(cache, stage / "pipeline-cache-initial.bin")
if hashlib.sha256(cache.read_bytes()).digest() != hashlib.sha256(
    (stage / "pipeline-cache-initial.bin").read_bytes()).digest():
    raise SystemExit("Cache changed while preparing replay")
(stage / "source-info.json").write_text(json.dumps({
    "capture": str(root), "device_snapshot": str(stage / "device-create.json"),
    "cache_sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
    "scope": "device configuration comes from a separate capture, not this GB10 run",
}, indent=2) + "\n")
PY
  step=validate-shaders
  spirv-val --target-env vulkan1.3 "${stage}/vertex.spv" > "${stage}/vertex-validation.log" 2>&1
  spirv-val --target-env vulkan1.3 "${stage}/fragment.spv" > "${stage}/fragment-validation.log" 2>&1
  step=build
  command=(
    cc -std=c11 -O2 -Wall -Wextra -Werror
    -I"${stage}" -I"${ue_dir}/Engine/Source/ThirdParty/Vulkan/Include"
    "${stage}/vulkan-graphics-replay.c" -lvulkan
    -o "${stage}/vulkan-graphics-replay"
  )
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" \
    > "${stage}/build-command.json"
  "${command[@]}" > "${stage}/build.log" 2>&1
  file "${stage}/vulkan-graphics-replay" > "${stage}/binary-architecture.txt"
  grep -q 'ARM aarch64' "${stage}/binary-architecture.txt"
  sha256sum "${stage}/vulkan-graphics-replay" "${stage}"/*.c "${stage}"/*.py \
    "${stage}"/*.h "${stage}"/*.spv "${stage}"/*.bin \
    "${stage}/device-create.json" "${stage}/driver-input.json" \
    "${stage}/source-info.json" \
    "${stage}/build-command.json" > "${stage}/inputs.sha256"
else
  step=verify
  (cd "${stage}" && sha256sum --check inputs.sha256) > "${stage}/input-validation.log" 2>&1
  export VK_ICD_FILENAMES="${CARLA_GB10_ICD:-/etc/vulkan/icd.d/nvidia_icd.json}"
  export XDG_RUNTIME_DIR="${stage}/xdg"
  mkdir -m 0700 "${XDG_RUNTIME_DIR}"
  timeout --signal=TERM --kill-after=5 30 vulkaninfo --summary \
    > "${stage}/vulkaninfo.log" 2>&1
  grep -Eq '^[[:space:]]*deviceName[[:space:]]*=[[:space:]]*NVIDIA GB10[[:space:]]*$' \
    "${stage}/vulkaninfo.log"
  step=run
  command=(timeout --signal=TERM --kill-after=5 30
    "${stage}/vulkan-graphics-replay" "${stage}/vertex.spv"
    "${stage}/fragment.spv" "${stage}/pipeline-cache-initial.bin")
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" \
    > "${stage}/run-command.json"
  "${command[@]}" > "${stage}/result.json" 2> "${stage}/run.log"
  python3 - "${stage}/result.json" "${stage}/device-create.json" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
result = json.loads(Path(sys.argv[1]).read_text())
digest = hashlib.sha256(Path(sys.argv[2]).read_bytes()).hexdigest()
if (result.get("status") != "PASS" or result.get("backend") != "NVIDIA GB10"
    or result.get("device_snapshot_sha256") != digest
    or result.get("ue_exact_replay") is not False):
    raise SystemExit("Graphics replay result does not match prepared inputs")
PY
fi
step=complete
