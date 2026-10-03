#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

port="${CARLA_UE_SHUTDOWN_PORT:-20221}"
startup_timeout="${CARLA_UE_SHUTDOWN_STARTUP_TIMEOUT:-120}"
settle="${CARLA_UE_SHUTDOWN_SETTLE:-10}"
grace="${CARLA_UE_SHUTDOWN_GRACE:-60}"
client_root="${CARLA_COOKED_CLIENT_ROOT:-/artifacts/carla/cooked-client-full/CarlaUnreal}"
binary="${client_root}/Binaries/LinuxArm64/CarlaUnreal"
debug_binary="${CARLA_DEBUG_BINARY:-/workspace/carla/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal.debug}"
wheel="${CARLA_RUNTIME_WHEEL:-/artifacts/carla/cmake-arm64/PythonAPI/dist/carla-0.10.0-cp310-cp310-linux_aarch64.whl}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
map="/Game/Carla/Maps/Town10HD_Opt"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/arm64-renderer-scope.sh"

for value in "${port}" "${startup_timeout}" "${settle}" "${grace}"; do
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || {
    echo "Shutdown capture limits must be positive integers" >&2
    exit 64
  }
done
(( port >= 1024 && port <= 65533 && startup_timeout <= 300 && settle <= 600 && grace <= 600 )) || {
  echo "Shutdown capture limits are out of range" >&2
  exit 64
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run this script in a native ARM64 carla-build container" >&2
  exit 2
}
[[ -x "${binary}" && -f "${debug_binary}" && -f "${client_root}/AssetRegistry.bin" && -f "${wheel}" ]] || {
  echo "Staged ARM64 cooked client, matching debug symbols and runtime wheel are required" >&2
  exit 2
}
for path in \
  "${client_root}/Content/Carla/Maps/Town10HD_Opt.umap" \
  "${client_root}/Content/Carla/Maps/OpenDrive/Town10HD_Opt.xodr" \
  "${client_root}/Content/Carla/Maps/Nav/Town10HD_Opt.bin"; do
  [[ -f "${path}" ]] || { echo "Required NullRHI input is missing: ${path}" >&2; exit 2; }
done
command -v gdb >/dev/null || { echo "gdb is required" >&2; exit 2; }
# The debugger is only usable with this capability, and without the check the failure mode is
# an opaque gdb error long after the run directory was created.
cap_eff="$(awk '/^CapEff:/ {print $2}' /proc/self/status)"
[[ "${cap_eff}" =~ ^[0-9a-fA-F]+$ ]] || { echo "Cannot read CapEff from /proc/self/status" >&2; exit 2; }
(( (0x${cap_eff} >> 19) & 1 )) || {
  echo "CAP_SYS_PTRACE is required (run with --cap-add SYS_PTRACE --security-opt seccomp=unconfined)" >&2
  exit 2
}

python3 - "${port}" <<'PY'
import socket
import sys

with socket.socket() as listener:
    # SO_REUSEADDR keeps an immediately preceding run's TIME_WAIT sockets from
    # failing a port that no process is actually listening on.
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", int(sys.argv[1])))
PY

run_dir="$(mktemp -d "${artifact_dir}/ue-shutdown-crash-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
gdb_pid=""
inferior_pid=""
watchdog_pid=""
analysis_status=""
analysis_path="${run_dir}/shutdown-analysis.json"

finish() {
  local code=$? status=FAIL shutdown=unknown
  trap - EXIT INT TERM
  set +e
  if [[ -n "${watchdog_pid}" ]]; then
    kill -TERM "${watchdog_pid}" 2>/dev/null
  fi
  # Only a pid that already passed the /proc/<pid>/exe check is ever signalled. The first
  # attempt at this capture used `pgrep -f <port>` from the shell, which matched the probe's
  # own command line and killed PID 1.
  if [[ "${inferior_pid}" =~ ^[0-9]+$ ]] && (( inferior_pid > 1 )); then
    kill -TERM "${inferior_pid}" 2>/dev/null
    for _ in $(seq 1 "${grace}"); do
      kill -0 "${inferior_pid}" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "${inferior_pid}" 2>/dev/null
  fi
  if [[ "${gdb_pid}" =~ ^[0-9]+$ ]]; then
    kill -TERM "${gdb_pid}" 2>/dev/null
    for _ in $(seq 1 10); do
      kill -0 "${gdb_pid}" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "${gdb_pid}" 2>/dev/null
    wait "${gdb_pid}" 2>/dev/null
  fi
  if [[ -f "${analysis_path}" ]]; then
    # One read of the analysis file, which is the single source of truth for both the status
    # and the exit evidence.
    read -r analysis_status inferior_exit observed_signals < <(python3 - "${analysis_path}" <<'PY' || true
import json
import sys

report = json.load(open(sys.argv[1]))
exit_info = report.get("exit") or {}
code = exit_info.get("effective_code")
signals = ",".join(event.get("signal", "?") for event in report.get("stop_events", []))
print(report["status"], "unknown" if code is None else code, signals or "none")
PY
)
  fi
  inferior_exit="${inferior_exit:-unknown}"
  observed_signals="${observed_signals:-none}"
  case "${analysis_status}" in
    CAPTURED_TARGET_ASSERT) status=PASS ;;
    NO_TARGET_ASSERT) status=NO_EVIDENCE ;;
    INCONCLUSIVE) status=INCONCLUSIVE ;;
    *) status=FAIL ;;
  esac
  # Same two-part classification as the NullRHI stop path, and the same reason: UE requests
  # exit with 128+signal, so a handled shutdown and an unhandled kill both report 143, while a
  # crash signal is unambiguous.
  case "${inferior_exit:-}" in
    0) shutdown=exited ;;
    139|134|135|136) shutdown=crashed ;;
    *) if grep -qE "LogExit: (Preparing to exit|Exiting)" "${run_dir}/server.log" 2>/dev/null; then
         shutdown=graceful
       fi ;;
  esac
  printf "# UE Shutdown Crash Capture\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Analysis status: %s\n- Target check: %s:%s %s\n- Inferior exit: %s\n- Shutdown: %s\n- Signals observed: %s\n- Inferior discovery: %s\n- Map: %s\n- Port: %s\n" \
    "${status}" "${step}" "${code}" "${analysis_status:-none}" \
    "RenderCore/Private/GPUMessaging.cpp" "68" "MessageHandlers.Contains(MessageId)" \
    "${inferior_exit:-unknown}" "${shutdown}" "${observed_signals:-none}" \
    "${discovery:-not-run}" "${map}" "${port}" > "${run_dir}/decision.md"
  printf -- "- Scope: gdb first-现场 capture of the failed GPUMessaging check on the NullRHI shutdown path; no engine patch, no rendering, no product acceptance\n" \
    >> "${run_dir}/decision.md"
  printf -- "- Boundary: a debugger makes FPlatformMisc::IsDebuggerPresent() true, so CheckVerifyFailedImpl2 skips AssertFailedImplV and the caller runs PLATFORM_BREAK() instead of the fatal-exit path; the observed exit 139 can therefore not be reproduced verbatim under gdb, and this run is evidence about the failed invariant and its caller, not about the exit status\n" \
    >> "${run_dir}/decision.md"
  printf "%s ue-shutdown-crash artifacts=%s step=%s status=%s exit=%s\n" \
    "${status}" "${run_dir}" "${step}" "${analysis_status:-none}" "${code}"
  exit "${code}"
}
trap finish EXIT

step=client-install
python3 -m pip install --no-index --no-deps --force-reinstall "${wheel}" \
  > "${run_dir}/client-install.log" 2>&1

mapfile -t renderer_flags < <(carla_renderer_systemsettings_flags)
inferior_args=(
  "${binary}" "${map}"
  -carla-rpc-port="${port}"
  -nullrhi -no-rendering -nosound -NoSplash -unattended -notraceserver
  "${renderer_flags[@]}"
)
cp -a "${BASH_SOURCE[0]}" "${script_dir}/dump-ue-shutdown.gdb" \
  "${script_dir}/gdb_ue_shutdown_capture.py" \
  "${script_dir}/analyze_ue_shutdown_capture.py" \
  "${script_dir}/arm64-renderer-scope.sh" "${run_dir}/"
export CARLA_GDB_SCRIPTS="${run_dir}"
export CARLA_UE_SHUTDOWN_CAPTURE_DIR="${run_dir}"
export CARLA_UE_SHUTDOWN_GDB_LOG="${run_dir}/gdb-messages.log"

command=(
  gdb -q -nx -batch
  -ex "file ${binary}" -ex "symbol-file ${debug_binary}"
  -x "${run_dir}/dump-ue-shutdown.gdb"
  -ex run
  -ex 'python gdb_ue_shutdown_capture.state.finish()'
  -ex quit
  --args "${inferior_args[@]}"
)
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" \
  > "${run_dir}/command.json"
python3 -c 'import json,os; print(json.dumps({key:os.environ.get(key) for key in ("PATH","LD_LIBRARY_PATH","PYTHONHOME","CARLA_GDB_SCRIPTS","CARLA_UE_SHUTDOWN_CAPTURE_DIR","CARLA_DEBUG_BINARY","CARLA_UE_SHUTDOWN_PORT")}))' \
  > "${run_dir}/environment.json"
sha256sum "${run_dir}"/*.py "${run_dir}"/*.gdb "${run_dir}"/*.sh \
  "${run_dir}/command.json" "${run_dir}/environment.json" "${binary}" "${debug_binary}" \
  "$(command -v gdb)" "$(command -v python3)" > "${run_dir}/inputs.sha256"

step=launch
# `exec` matters: without it bash keeps the subshell and forks gdb, so gdb_pid would name the
# wrapper and the exit status would be the wrapper's.
(
  cd "${client_root}"
  exec "${command[@]}" > "${run_dir}/server.log" 2>&1
) &
gdb_pid=$!
max_wall=$((startup_timeout + settle + grace + 120))
(
  sleep "${max_wall}"
  kill -TERM "${gdb_pid}" 2>/dev/null
  sleep 5
  kill -KILL "${gdb_pid}" 2>/dev/null
) > /dev/null 2>&1 &
watchdog_pid=$!

step=discover
# The inferior is found through the kernel's own record of gdb's children and then verified
# by executable path, so the probe can never signal itself, the debugger or PID 1.
expected_exe="$(readlink -f "${binary}")"
discovery="not-found"
for _ in $(seq 1 160); do
  for candidate in $(cat /proc/"${gdb_pid}"/task/*/children 2>/dev/null); do
    [[ "${candidate}" =~ ^[0-9]+$ ]] || continue
    (( candidate > 1 )) || continue
    [[ "${candidate}" != "${gdb_pid}" && "${candidate}" != "$$" ]] || continue
    [[ -e "/proc/${candidate}/exe" ]] || continue
    [[ "$(readlink -f "/proc/${candidate}/exe" 2>/dev/null)" == "${expected_exe}" ]] || continue
    inferior_pid="${candidate}"
    discovery="/proc/${gdb_pid}/task/*/children, exe-verified against ${expected_exe}"
    break
  done
  [[ -n "${inferior_pid}" ]] && break
  kill -0 "${gdb_pid}" 2>/dev/null || break
  sleep 0.25
done
[[ -n "${inferior_pid}" ]] || { echo "Could not identify the gdb inferior" >&2; exit 3; }
printf '%s\n' "${inferior_pid}" > "${run_dir}/inferior-pid.txt"
printf '%s\n' "${discovery}" > "${run_dir}/inferior-discovery.txt"

step=ready
# Readiness is the RPC world, not a wall-clock wait: the earlier hand-run reproducer waited
# ~30 s and could not tell "scene registered its sockets" from "process happened to be up".
ready=0
for _ in $(seq 1 "${startup_timeout}"); do
  if timeout --signal=TERM --kill-after=2 5 python3 - "${port}" <<'PY' > "${run_dir}/ready.log" 2>&1
import carla
import sys

client = carla.Client("127.0.0.1", int(sys.argv[1]))
client.set_timeout(2.0)
print(client.get_server_version())
world = client.get_world()
assert world.get_map().name.endswith("Town10HD_Opt")
print(world.get_map().name)
PY
  then
    ready=1
    break
  fi
  kill -0 "${inferior_pid}" 2>/dev/null || break
  sleep 1
done
[[ "${ready}" -eq 1 ]] || { echo "The NullRHI server never exposed the expected RPC world" >&2; exit 3; }
sleep "${settle}"

step=signal
kill -0 "${inferior_pid}" 2>/dev/null || { echo "The inferior exited before shutdown was requested" >&2; exit 3; }
kill -TERM "${inferior_pid}"
printf '%s\n' "SIGTERM=${inferior_pid}" > "${run_dir}/shutdown-signal.txt"

step=stopped
gdb_code=0
wait "${gdb_pid}" || gdb_code=$?
printf '%s\n' "${gdb_code}" > "${run_dir}/gdb-exit-code.txt"

step=validate
analysis_code=0
python3 "${run_dir}/analyze_ue_shutdown_capture.py" --run-dir "${run_dir}" \
  > "${run_dir}/validation.log" 2>&1 || analysis_code=$?
sha256sum "${run_dir}/shutdown-capture.json" "${run_dir}/shutdown-analysis.json" \
  >> "${run_dir}/inputs.sha256" 2>/dev/null
if [[ "${analysis_code}" != 0 ]]; then
  tail -n 40 "${run_dir}/validation.log" >&2
fi

step=captured
exit "${analysis_code}"
