#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

mode="${CARLA_RUNTIME_MODE:-rpc}"
ticks="${CARLA_RUNTIME_TICKS:-100}"
port="${CARLA_RUNTIME_PORT:-2000}"
host="${CARLA_RUNTIME_HOST:-127.0.0.1}"
timeout_seconds="${CARLA_RUNTIME_TIMEOUT:-10}"
total_timeout="${CARLA_RUNTIME_TOTAL_TIMEOUT:-600}"
[[ "${mode}" == rpc || "${mode}" == sensors || "${mode}" == actors ]] || { echo "CARLA_RUNTIME_MODE must be rpc, sensors or actors" >&2; exit 64; }
for value in "${ticks}" "${port}" "${timeout_seconds}" "${total_timeout}"; do
  [[ "${value}" =~ ^[1-9][0-9]*$ && ${#value} -le 6 ]] || { echo "Runtime limits must be positive integers" >&2; exit 64; }
done
(( port <= 65535 && ticks <= 10000 && timeout_seconds <= 120 )) || {
  echo "Runtime limits are out of range" >&2; exit 64;
}
[[ "${mode}" == rpc || "${ticks}" -ge 2 ]] || { echo "${mode} validation needs at least two ticks" >&2; exit 64; }
[[ "${host}" =~ ^[0-9A-Za-z._-]+$ ]] || { echo "CARLA_RUNTIME_HOST must be a valid hostname or IPv4 address" >&2; exit 64; }
[[ "${CARLA_ALLOW_WORLD_MUTATION:-0}" == 1 ]] || {
  echo "Set CARLA_ALLOW_WORLD_MUTATION=1 only for an idle, exclusively owned test world" >&2; exit 64;
}
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in native ARM64 Docker on DGX Spark" >&2
  exit 2
fi
provenance="${CARLA_RUNTIME_PROVENANCE:-}"
wheel="${CARLA_RUNTIME_WHEEL:-/artifacts/carla/cmake-arm64/PythonAPI/dist/carla-0.10.0-cp310-cp310-linux_aarch64.whl}"
[[ "${provenance}" == /* && -f "${provenance}" ]] || {
  echo "CARLA_RUNTIME_PROVENANCE must name an existing build provenance JSON inside the container" >&2; exit 2;
}
[[ -f "${wheel}" ]] || { echo "Native CARLA wheel is missing: ${wheel}" >&2; exit 2; }
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/runtime-${mode}-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
inputs_identity=""
finish() {
  local code=$? final_code status=FAIL
  trap - EXIT
  set +e
  python3 "${script_dir}/runtime_entry_report.py" finalize --run-dir "${run_dir}" \
    --mode "${mode}" --exit-code "${code}" --input-sha256 "${inputs_identity}"
  final_code=$?
  [[ "${code}" != 0 ]] || code="${final_code}"
  [[ "${code}" == 0 && -s "${run_dir}/stage-report.json" ]] && status=PASS
  printf "# Native CARLA Client Invocation\n\n- Status: %s\n- Exit code: %s\n- Endpoint: %s:%s\n- Mode: %s\n- Scope: endpoint checks only; server build/architecture must be established separately\n" \
    "${status}" "${code}" "${host}" "${port}" "${mode}" > "${run_dir}/decision.md"
  printf "runtime artifacts=%s exit=%s\n" "${run_dir}" "${code}"
  exit "${code}"
}
trap finish EXIT
sha256sum "${wheel}" > "${run_dir}/wheel.sha256"
inputs_identity="$(python3 "${script_dir}/runtime_entry_report.py" capture \
  --output "${run_dir}/inputs.json" --file wheel "${wheel}" \
  --file wrapper "${BASH_SOURCE[0]}" --file evaluator "${script_dir}/check_carla_runtime.py" \
  --file stage-writer "${script_dir}/stage_report.py" --file provenance "${provenance}" \
  --file entry-reporter "${script_dir}/runtime_entry_report.py")"
[[ "${inputs_identity}" =~ ^[0-9a-f]{64}$ ]] || { echo "Invalid input capture identity" >&2; exit 2; }
verify_inputs() {
  python3 "${script_dir}/runtime_entry_report.py" verify-inputs --run-dir "${run_dir}" \
    --input-sha256 "${inputs_identity}"
  sha256sum --check "${run_dir}/wheel.sha256"
}
verify_inputs
python3 -c "import sys; print(sys.version)" > "${run_dir}/python-version.txt"
# The existing wheel is installed only into this disposable container.
install_command=(python3 -m pip install --no-index --no-deps --force-reinstall "${wheel}")
python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "${install_command[@]}" \
  > "${run_dir}/client-install.command.json"
"${install_command[@]}" > "${run_dir}/client-install.log" 2>&1
verify_inputs
command=(
  timeout --signal=INT --kill-after=30 "${total_timeout}"
  python3 "${script_dir}/check_carla_runtime.py"
  --mode "${mode}" --host "${host}" --port "${port}" --ticks "${ticks}"
  --timeout "${timeout_seconds}" --run-dir "${run_dir}/endpoint" --provenance "${provenance}"
  --allow-world-mutation
)
python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "${command[@]}" > "${run_dir}/entry-command.json"
verify_inputs
if "${command[@]}" > "${run_dir}/client.log" 2>&1; then
  tail -n 20 "${run_dir}/client.log"
else
  code=$?
  tail -n 80 "${run_dir}/client.log"
  exit "${code}"
fi
verify_inputs
