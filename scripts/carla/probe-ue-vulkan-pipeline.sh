#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

client_root="${CARLA_COOKED_CLIENT_ROOT:-/artifacts/carla/cooked-client-full/CarlaUnreal}"
binary="${CARLA_UE_PIPELINE_BINARY:-${client_root}/Binaries/LinuxArm64/CarlaUnreal}"
debug_binary="${CARLA_DEBUG_BINARY:-/workspace/carla/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal.debug}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
port="${CARLA_UE_PIPELINE_PORT:-20211}"
timeout_seconds="${CARLA_UE_PIPELINE_TIMEOUT:-120}"
target="${CARLA_UE_PIPELINE_TARGET:-FRDGMemcpyCS}"
mode="${CARLA_UE_PIPELINE_MODE:-entry}"
intervention="${CARLA_UE_PIPELINE_INTERVENTION:-none}"
defer_spirv_validation="${CARLA_UE_PIPELINE_DEFER_SPIRV_VALIDATION:-0}"
allow_device_only="${CARLA_UE_PIPELINE_ALLOW_DEVICE_ONLY:-0}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/arm64-vulkan-diagnostic-scope.sh"
vk_icd="${CARLA_VK_ICD_FILENAMES:-}"

[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ && "${timeout_seconds}" -le 300 ]] || {
  echo "CARLA_UE_PIPELINE_TIMEOUT must be 1..300" >&2; exit 64;
}
[[ "${port}" =~ ^[1-9][0-9]*$ && "${port}" -ge 1024 && "${port}" -le 65532 ]] || {
  echo "CARLA_UE_PIPELINE_PORT must be 1024..65532" >&2; exit 64;
}
[[ -n "${target}" && "${#target}" -le 128 ]] || {
  echo "CARLA_UE_PIPELINE_TARGET must have 1..128 characters" >&2; exit 64;
}
[[ "${mode}" == entry || "${mode}" == observe || "${mode}" == crash ]] || {
  echo "CARLA_UE_PIPELINE_MODE must be entry, observe or crash" >&2; exit 64;
}
[[ "${intervention}" == none || "${intervention}" == allocator-null || "${intervention}" == cache-null ]] || {
  echo "CARLA_UE_PIPELINE_INTERVENTION must be none, allocator-null or cache-null" >&2; exit 64;
}
[[ "${intervention}" == none || "${mode}" == observe ]] || {
  echo "A target intervention requires observe mode and stops before UE resumes after the call" >&2; exit 64;
}
[[ "${defer_spirv_validation}" == 0 || "${defer_spirv_validation}" == 1 ]] || {
  echo "CARLA_UE_PIPELINE_DEFER_SPIRV_VALIDATION must be 0 or 1" >&2; exit 64;
}
[[ "${allow_device_only}" == 0 || "${allow_device_only}" == 1 ]] || {
  echo "CARLA_UE_PIPELINE_ALLOW_DEVICE_ONLY must be 0 or 1" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 && -x "${binary}" && -f "${debug_binary}" ]] || {
  echo "Native ARM64 Docker and staged client/debug symbols are required" >&2; exit 2;
}
command -v gdb >/dev/null
if [[ "${defer_spirv_validation}" == 0 ]]; then
  command -v spirv-val >/dev/null
fi
if [[ -z "${vk_icd}" ]]; then
  vk_icd="$(find /usr/share/vulkan/icd.d -maxdepth 1 -type f -name 'lvp_icd*.json' -print -quit)"
fi
[[ -f "${vk_icd}" ]] || { echo "Vulkan ICD is missing: ${vk_icd}" >&2; exit 2; }

run_dir="$(mktemp -d "${artifact_dir}/ue-vulkan-pipeline-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=capture
finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 && "${step}" == complete ]] && status=PASS
  [[ "${code}" == 0 && "${step}" == device-only ]] && status=CAPTURED_DEVICE_ONLY
  printf "# UE Vulkan Pipeline Capture\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Target: %s\n- Mode: %s\n- Intervention: %s\n- Scope: API input capture, optionally observing return; no runtime/sensor acceptance; initial cache only, not final shared cache state; UE handled-ensure SIGTRAP suppressed by GDB; optional single-call register intervention, owned inferior killed before UE resumes\n" \
    "${status}" "${step}" "${code}" "${target}" "${mode}" "${intervention}" > "${run_dir}/decision.md"
  printf "%s ue-vulkan-pipeline artifacts=%s step=%s exit=%s\n" "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT
cp -a "${BASH_SOURCE[0]}" "${script_dir}/dump-ue-vulkan-pipeline.gdb" \
  "${script_dir}/gdb_vulkan_pipeline_capture.py" "${script_dir}/gdb_vulkan_device_capture.py" \
  "${script_dir}/vulkan_pipeline_snapshot.py" "${script_dir}/vulkan_device_snapshot.py" \
  "${script_dir}/arm64-vulkan-diagnostic-scope.sh" "${script_dir}/arm64-renderer-scope.sh" "${run_dir}/"
export CARLA_GDB_SCRIPTS="${run_dir}"
export CARLA_UE_PIPELINE_CAPTURE_DIR="${run_dir}"
export CARLA_UE_PIPELINE_TARGET="${target}"
export CARLA_UE_PIPELINE_TIMEOUT="${timeout_seconds}"
export CARLA_UE_PIPELINE_MODE="${mode}"
export CARLA_UE_PIPELINE_INTERVENTION="${intervention}"
export CARLA_UE_VULKAN_DEVICE_STATE="${run_dir}/device-create.json"

client_args=(/Game/Carla/Maps/Town10HD_Opt -carla-rpc-port="${port}")
while IFS= read -r flag; do
  client_args+=("${flag}")
done < <(carla_vulkan_diagnostic_flags)
client_args+=('-ini:Engine:[ConsoleVariables]:g.TimeoutForBlockOnRenderFence=300000')
command=(
  timeout --signal=TERM --kill-after=5 "$((timeout_seconds + 20))"
  gdb -q -nx -batch -ex "file ${binary}" -ex "symbol-file ${debug_binary}"
  -x "${run_dir}/dump-ue-vulkan-pipeline.gdb" -ex run
  -ex 'python gdb_vulkan_pipeline_capture.state.finish()'
  -ex quit --args "${binary}" "${client_args[@]}"
)
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" > "${run_dir}/command.json"
sha256sum "${run_dir}"/*.py "${run_dir}"/*.sh "${run_dir}"/*.gdb \
  "${run_dir}/command.json" "${binary}" "${debug_binary}" "${vk_icd}" > "${run_dir}/inputs.sha256"
sha256sum "$(command -v gdb)" "$(command -v python3)" >> "${run_dir}/inputs.sha256"
if [[ "${defer_spirv_validation}" == 0 ]]; then
  sha256sum "$(command -v spirv-val)" >> "${run_dir}/inputs.sha256"
fi
python3 -c 'import json,os; print(json.dumps({name:os.environ.get(name) for name in ("PATH","LD_LIBRARY_PATH","PYTHONHOME","CARLA_UE_PIPELINE_TARGET","CARLA_UE_PIPELINE_TIMEOUT","CARLA_UE_PIPELINE_MODE","CARLA_UE_PIPELINE_INTERVENTION")}))' \
  > "${run_dir}/environment.json"
sha256sum "${run_dir}/environment.json" >> "${run_dir}/inputs.sha256"
mkdir -m 0700 "${run_dir}/xdg"
(
  cd "${client_root}"
  env VK_ICD_FILENAMES="${vk_icd}" XDG_RUNTIME_DIR="${run_dir}/xdg" "${command[@]}"
) > "${run_dir}/gdb.log" 2>&1

step=validate
validator_args=(--run-dir "${run_dir}")
[[ "${intervention}" == none ]] || validator_args+=(--allow-intervention)
device_validation_code=0
python3 "${run_dir}/vulkan_device_snapshot.py" --input "${run_dir}/device-create.json" \
  > "${run_dir}/device-validation.log" 2>&1 || device_validation_code=$?
pipeline_validation_code=0
python3 "${run_dir}/vulkan_pipeline_snapshot.py" "${validator_args[@]}" \
  > "${run_dir}/validation.log" 2>&1 || pipeline_validation_code=$?
if [[ "${pipeline_validation_code}" != 0 ]]; then
  if [[ "${allow_device_only}" == 1 && "${mode}" == crash
        && "${device_validation_code}" == 0
        && -f "${run_dir}/pipeline-capture.json" ]] &&
     python3 - "${run_dir}/pipeline-capture.json" > "${run_dir}/device-only-reason.json" <<'PY'
import json
import sys
from pathlib import Path
state = json.loads(Path(sys.argv[1]).read_text())
if state.get("capture_complete") is True or state.get("errors"):
    raise SystemExit(1)
print(json.dumps({
    "status": "CAPTURED_DEVICE_ONLY",
    "pipeline_capture_complete": False,
    "pipeline_errors": state.get("errors", []),
    "target_call_present": state.get("target_call") is not None,
    "scope": "same-run vkCreateDevice snapshot; pipeline object capture incomplete",
}, sort_keys=True))
PY
  then
    step=device-only
  else
    exit "${pipeline_validation_code}"
  fi
elif [[ "${device_validation_code}" != 0 ]]; then
  exit "${device_validation_code}"
fi
if [[ "${step}" == device-only ]]; then
  printf '%s\n' "deferred-to-build-container" > "${run_dir}/spirv-validation.log"
  exit 0
fi
if [[ "${defer_spirv_validation}" == 1 ]]; then
  printf '%s\n' "deferred-to-build-container" > "${run_dir}/spirv-validation.log"
else
  spirv-val --target-env vulkan1.3 "${run_dir}/shader.spv" > "${run_dir}/spirv-validation.log" 2>&1
fi
sha256sum "${run_dir}/pipeline-capture.json" "${run_dir}/device-create.json" \
  >> "${run_dir}/inputs.sha256"
if [[ -f "${run_dir}/shader.spv" ]]; then
  sha256sum "${run_dir}/shader.spv" "${run_dir}/ue-layout.txt" \
    "${run_dir}/pipeline-cache-initial.bin" >> "${run_dir}/inputs.sha256"
fi
if [[ "${mode}" == observe || "${mode}" == crash ]]; then
  step=driver-return
  python3 "${run_dir}/vulkan_pipeline_snapshot.py" "${validator_args[@]}" \
    --require-driver-return > "${run_dir}/driver-observation.log" 2>&1
fi
step=complete
