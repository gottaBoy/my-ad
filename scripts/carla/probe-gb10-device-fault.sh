#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
baseline="${CARLA_GB10_FAULT_BASELINE:-}"
port="${CARLA_GB10_FAULT_PORT:-20260}"
seconds="${CARLA_GB10_FAULT_SECONDS:-90}"
cache_lifecycle="${CARLA_GB10_FAULT_CACHE_LIFECYCLE:-0}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
debug="${CARLA_DEBUG_BINARY:-/workspace/carla/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal.debug}"
icd="${CARLA_GB10_ICD:-/etc/vulkan/icd.d/nvidia_icd.json}"

[[ "${port}" =~ ^[1-9][0-9]*$ && "${port}" -ge 1024 && "${port}" -le 65532
   && "${seconds}" =~ ^[1-9][0-9]*$ && "${seconds}" -le 300 ]] || {
  echo "Port must be 1024..65532 and timeout 1..300" >&2; exit 64;
}
[[ "${cache_lifecycle}" == 0 || "${cache_lifecycle}" == 1 ]] || {
  echo "CARLA_GB10_FAULT_CACHE_LIFECYCLE must be 0 or 1" >&2; exit 64;
}
[[ "${baseline}" == "${artifact_dir}"/town10-gb10-rpc-* && -d "${baseline}"
   && ! -L "${baseline}" && -f "${baseline}/server-command.json"
   && -f "${baseline}/decision.md" && -f "${debug}" && -f "${icd}"
   && -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Native GB10, archived fault baseline and debug binary required" >&2; exit 2;
}
command -v gdb >/dev/null
command -v vulkaninfo >/dev/null

run_dir="$(mktemp -d "${artifact_dir}/gb10-device-fault-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
finish() {
  local code=$? status=FAIL
  trap - EXIT INT TERM
  [[ "${code}" == 0 && "${step}" == complete ]] && status=CAPTURED_FAULT
  printf "# GB10 Device + Fault Diagnostic\n\n- Status: %s\n- Step: %s\n- Exit: %s\n- Scope: GDB captures instance/device creation only; pipeline history is engine-side; no world or sensor acceptance\n" \
    "${status}" "${step}" "${code}" > "${run_dir}/decision.md"
  printf "%s gb10-device-fault artifacts=%s step=%s exit=%s\n" \
    "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

export VK_ICD_FILENAMES="${icd}"
export XDG_RUNTIME_DIR="${run_dir}/xdg"
mkdir -m 0700 "${XDG_RUNTIME_DIR}"
timeout --signal=TERM --kill-after=5 30 vulkaninfo --summary \
  > "${run_dir}/vulkaninfo.log" 2>&1
grep -Eq '^[[:space:]]*deviceName[[:space:]]*=[[:space:]]*NVIDIA GB10[[:space:]]*$' \
  "${run_dir}/vulkaninfo.log"
cp -a "${script_dir}/dump-ue-vulkan-device-fault.gdb" \
  "${script_dir}/gdb_vulkan_device_capture.py" \
  "${script_dir}/vulkan_device_snapshot.py" \
  "${script_dir}/analyze_vulkan_driver_entries.py" \
  "${script_dir}/analyze_vulkan_cache_lifecycle.py" "${run_dir}/"
export CARLA_GDB_SCRIPTS="${run_dir}"
export CARLA_UE_VULKAN_DEVICE_STATE="${run_dir}/device-create.json"
export CARLA_GDB_CONTINUE_AFTER_DEVICE=1

# Preserve the exact archived renderer/diagnostic options; only the owned
# port and output directory change. The baseline binary must be the same file.
python3 - "${baseline}" "${run_dir}" "${port}" <<'PY' > "${run_dir}/launch-args.nul"
import json
import os
from pathlib import Path
import re
import sys

baseline, run, port = sys.argv[1:]
args = json.loads((Path(baseline) / "server-command.json").read_text())
if (not isinstance(args, list) or len(args) < 8 or
    args[:2] != ["timeout", "--signal=INT"] or
    not args[2].startswith("--kill-after=") or
    not re.fullmatch(r"[0-9]+", args[3]) or
    not args[4].endswith("/Binaries/LinuxArm64/CarlaUnreal") or
    not Path(args[4]).is_file() or
    "-CarlaVulkanSerializeMixedPipelineCreation" not in args or
    not any(x.startswith("-CarlaVulkanPipelineHistory=") for x in args) or
    not any(x.startswith("-CarlaVulkanPipelineHistoryTarget=") for x in args)):
    raise SystemExit("baseline is not the expected mixed driver-entry capture")
args = args[4:]
ports = [i for i, value in enumerate(args) if re.fullmatch(r"-carla-rpc-port=[0-9]+", value)]
if len(ports) != 1:
    raise SystemExit("baseline port is not unique")
args[ports[0]] = f"-carla-rpc-port={port}"
for i, value in enumerate(args):
    if value.startswith(("-CarlaVulkanPipelineHistory=", "-CarlaVulkanShaderDiagnostics=")):
        args[i] = value.replace(baseline, run, 1)
if not any(x == f"-CarlaVulkanPipelineHistory={run}" for x in args):
    raise SystemExit("history destination was not replaced")
if os.environ.get("CARLA_GB10_FAULT_CACHE_LIFECYCLE", "0") == "1":
    if "-CarlaVulkanCacheLifecycle" not in args:
        args.append("-CarlaVulkanCacheLifecycle")
Path(run, "replayed-server-command.json").write_text(json.dumps(args) + "\n")
sys.stdout.buffer.write(b"\0".join(x.encode() for x in args) + b"\0")
PY
mapfile -d '' -t client_args < "${run_dir}/launch-args.nul"
binary="${client_args[0]}"
[[ -x "${binary}" ]] || { echo "Baseline binary is not executable" >&2; exit 2; }
expected_sha="$(awk -v path="${binary}" '$2 == path {print $1}' "${baseline}/inputs.sha256")"
[[ "${expected_sha}" =~ ^[0-9a-f]{64}$ && "$(sha256sum "${binary}" | awk '{print $1}')" == "${expected_sha}" ]] || {
  echo "Baseline binary changed after evidence capture" >&2; exit 2;
}
sha256sum "${binary}" "${debug}" "${icd}" "${baseline}/server-command.json" \
  "${run_dir}"/*.py "${run_dir}"/*.gdb "${run_dir}/replayed-server-command.json" \
  > "${run_dir}/inputs.sha256"

step=capture
command=(
  timeout --signal=TERM --kill-after=5 "$((seconds + 15))"
  gdb -q -nx -batch -ex "file ${binary}" -ex "symbol-file ${debug}"
  -x "${run_dir}/dump-ue-vulkan-device-fault.gdb"
  -ex run -ex quit --args "${client_args[@]}"
)
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" \
  > "${run_dir}/command.json"
capture_code=0
(
  cd "$(dirname "$(dirname "$(dirname "${binary}")")")"
  "${command[@]}"
) > "${run_dir}/gdb.log" 2>&1 || capture_code=$?
printf '%s\n' "${capture_code}" > "${run_dir}/gdb-exit-code.txt"
step=validate
python3 "${run_dir}/vulkan_device_snapshot.py" \
  --input "${run_dir}/device-create.json" \
  > "${run_dir}/device-validation.log" 2>&1
python3 "${run_dir}/analyze_vulkan_driver_entries.py" \
  --run-dir "${run_dir}" --output "${run_dir}/driver-entry-analysis.json" \
  > "${run_dir}/driver-entry-analysis.log" 2>&1
if grep -Fq '"-CarlaVulkanCacheLifecycle"' "${run_dir}/replayed-server-command.json"; then
  python3 "${run_dir}/analyze_vulkan_cache_lifecycle.py" \
    --run-dir "${run_dir}" --output "${run_dir}/cache-lifecycle-analysis.json" \
    > "${run_dir}/cache-lifecycle-analysis.log" 2>&1
fi
python3 - "${run_dir}" <<'PY'
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
report = json.loads((root / "driver-entry-analysis.json").read_text())
log = (root / "gdb.log").read_text(errors="replace")
if (report["status"] != "CAPTURED_DRIVER_ENTRY" or len(report["unreturned"]) != 1 or
    "received signal SIGSEGV" not in log or "libnvidia-glvkspirv" not in log):
    raise SystemExit("GDB capture did not reach a correlated NVIDIA compiler fault")
PY
sha256sum "${run_dir}/device-create.json" "${run_dir}/driver-entry-analysis.json" \
  >> "${run_dir}/inputs.sha256"
step=complete
