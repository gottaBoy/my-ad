#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

port="${CARLA_NULLRHI_PORT:-20200}"
ticks="${CARLA_NULLRHI_TICKS:-20}"
startup_timeout="${CARLA_NULLRHI_STARTUP_TIMEOUT:-90}"
client_root="${CARLA_COOKED_CLIENT_ROOT:-/artifacts/carla/cooked-client-full/CarlaUnreal}"
binary="${client_root}/Binaries/LinuxArm64/CarlaUnreal"
wheel="${CARLA_RUNTIME_WHEEL:-/artifacts/carla/cmake-arm64/PythonAPI/dist/carla-0.10.0-cp310-cp310-linux_aarch64.whl}"
provenance="${CARLA_RUNTIME_PROVENANCE:-/artifacts/carla/cooked-server-full/runtime-provenance.json}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
map="/Game/Carla/Maps/Town10HD_Opt"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/arm64-renderer-scope.sh"

for value in "${port}" "${ticks}" "${startup_timeout}"; do
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || {
    echo "NullRHI limits must be positive integers" >&2
    exit 64
  }
done
(( port >= 1024 && port <= 65533 && ticks <= 1000 && startup_timeout <= 120 )) || {
  echo "NullRHI limits are out of range" >&2
  exit 64
}
server_timeout=$((startup_timeout + 120))
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in a native ARM64 carla-build container" >&2
  exit 2
fi
[[ -x "${binary}" && -f "${client_root}/AssetRegistry.bin" ]] || {
  echo "Staged ARM64 cooked client is missing: ${client_root}" >&2
  exit 2
}
for path in \
  "${client_root}/Content/Carla/Maps/Town10HD_Opt.umap" \
  "${client_root}/Content/Carla/Maps/OpenDrive/Town10HD_Opt.xodr" \
  "${client_root}/Content/Carla/Maps/Nav/Town10HD_Opt.bin" \
  "${wheel}" "${provenance}"; do
  [[ -f "${path}" ]] || { echo "Required NullRHI input is missing: ${path}" >&2; exit 2; }
done

python3 - "${port}" <<'PY'
import socket
import sys

with socket.socket() as listener:
    # SO_REUSEADDR keeps an immediately preceding run's TIME_WAIT sockets from
    # failing a port that no process is actually listening on.
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", int(sys.argv[1])))
PY

run_dir="$(mktemp -d "${artifact_dir}/town10-nullrhi-rpc-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
server_pid=""
watchdog_pid=""
# How long the server gets to run its exit sequence after SIGTERM before SIGKILL.
stop_grace="${CARLA_NULLRHI_STOP_GRACE:-20}"

finish() {
  local code=$? server_code=0
  trap - EXIT INT TERM
  set +e
  if [[ -n "${watchdog_pid}" ]]; then
    kill -TERM "${watchdog_pid}" 2>/dev/null
  fi
  if [[ -n "${server_pid}" ]]; then
    # server_pid is the server itself, not a timeout wrapper: GNU timeout does not
    # forward a SIGTERM it receives to its child, so signalling the wrapper left the
    # server orphaned for the container runtime to SIGKILL, and no signal ever reached
    # UE. Waiting on the server also makes server_code its real exit status.
    kill -TERM "${server_pid}" 2>/dev/null
    for _ in $(seq 1 "${stop_grace}"); do
      kill -0 "${server_pid}" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "${server_pid}" 2>/dev/null
    wait "${server_pid}"
    server_code=$?
  fi
  printf "%s\n" "${server_code}" > "${run_dir}/server-exit-code.txt"
  # Best effort, and deliberately two-part. The status is exact but ambiguous on its own:
  # UE's graceful handler requests exit with 128+signal, so a handled shutdown and an
  # unhandled kill both report 143. A crash signal is not ambiguous, so it wins. The log
  # is the only evidence that the exit sequence ran, and it is racy because a crash can
  # truncate the log mid-write, which is why it only ever upgrades the classification.
  shutdown=killed
  case "${server_code}" in
    0) shutdown=exited ;;
    139|134|135|136) shutdown=crashed ;;
    *) if grep -qE "LogExit: (Preparing to exit|Exiting)" "${run_dir}/server.log" 2>/dev/null; then
         shutdown=graceful
       fi ;;
  esac
  local status=FAIL
  [[ "${code}" -eq 0 && "${step}" == complete ]] && status=PASS
  printf "# Town10 NullRHI RPC\n\n- Endpoint status: %s\n- Step: %s\n- Exit code: %s\n- Server stop code: %s\n- Shutdown: %s\n- Map: %s\n- Port: %s\n- Ticks: %s\n- Scope: isolated cooked client RPC and synchronous ticks without rendering\n- Excluded: RGB/LiDAR, Lavapipe/GB10 Vulkan, and visual correctness\n" \
    "${status}" "${step}" "${code}" "${server_code}" "${shutdown}" "${map}" "${port}" "${ticks}" \
    > "${run_dir}/decision.md"
  printf "%s town10-nullrhi-rpc artifacts=%s step=%s exit=%s\n" \
    "${status}" "${run_dir}" "${step}" "${code}"
  exit "${code}"
}
trap finish EXIT

step=client-install
python3 -m pip install --no-index --no-deps --force-reinstall "${wheel}" \
  > "${run_dir}/client-install.log" 2>&1
sha256sum "${binary}" "${wheel}" "${provenance}" \
  "${BASH_SOURCE[0]}" "${script_dir}/check_carla_runtime.py" \
  "${script_dir}/arm64-renderer-scope.sh" > "${run_dir}/inputs.sha256"

mapfile -t renderer_flags < <(carla_renderer_systemsettings_flags)
server_command=(
  "${binary}" "${map}"
  -carla-rpc-port="${port}"
  -nullrhi -no-rendering -nosound -NoSplash -unattended -notraceserver
  "${renderer_flags[@]}"
)
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${server_command[@]}" \
  > "${run_dir}/server-command.json"
step=startup
# `exec` matters: without it bash keeps the subshell and forks the server, so server_pid
# names the subshell and any signal stops the wrapper while the server keeps running until
# the container runtime kills it.
(
  cd "${client_root}"
  exec "${server_command[@]}" > "${run_dir}/server.log" 2>&1
) &
server_pid=$!
# Wall-clock guard, kept from the timeout wrapper this used to run under. It signals the
# server itself, and finish kills it, so a completed run is not held to the guard.
(
  sleep "${server_timeout}"
  kill -TERM "${server_pid}" 2>/dev/null
) > /dev/null 2>&1 &
watchdog_pid=$!

# UE stdout can stop flushing during startup; use the actual RPC world/map instead
# of interpreting an absent "New episode" log line as a failed startup.
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
  kill -0 "${server_pid}" 2>/dev/null || break
  sleep 1
done
[[ "${ready}" -eq 1 ]] || {
  echo "Town10 cooked client did not expose the expected RPC world" >&2
  exit 3
}

step=rpc
command=(
  timeout --signal=TERM --kill-after=5 90
  python3 "${script_dir}/check_carla_runtime.py"
  --mode rpc --host 127.0.0.1 --port "${port}" --ticks "${ticks}"
  --timeout 10 --run-dir "${run_dir}/endpoint" --provenance "${provenance}"
  --allow-world-mutation
)
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${command[@]}" \
  > "${run_dir}/client-command.json"
if "${command[@]}" > "${run_dir}/client.log" 2>&1; then
  tail -n 6 "${run_dir}/client.log"
else
  code=$?
  tail -n 50 "${run_dir}/client.log" >&2
  exit "${code}"
fi

step=complete
