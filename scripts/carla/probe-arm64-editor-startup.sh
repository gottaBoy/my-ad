#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

timeout_seconds="${CARLA_EDITOR_STARTUP_TIMEOUT:-60}"
[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || {
  echo "CARLA_EDITOR_STARTUP_TIMEOUT must be positive" >&2
  exit 64
}
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi

ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
carla_dir="${CARLA_SOURCE_DIR:-/workspace/carla}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
editor="${ue_dir}/Engine/Binaries/LinuxArm64/UnrealEditor"
project="${carla_dir}/Unreal/CarlaUnreal/CarlaUnreal.uproject"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/editor-startup-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight

finish() {
  local code=$? status=BLOCKED
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 CarlaUnrealEditor Startup Probe\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Timeout: %ss\n- Scope: Editor initialization, plugin/target-platform loading, and clean exit\n- Excluded: Cook, Vulkan rendering, RPC, sensors, and full CARLA runtime support\n" \
    "${status}" "${step}" "${code}" "${timeout_seconds}" > "${run_dir}/decision.md"
  printf "%s editor-startup artifacts=%s step=%s exit=%s\n" \
    "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

[[ -x "${editor}" ]] || { echo "Editor binary is missing: ${editor}" >&2; exit 2; }
[[ -f "${project}" ]] || { echo "CarlaUnreal project is missing: ${project}" >&2; exit 2; }

command=(
  timeout --signal=INT --kill-after=10 "${timeout_seconds}" "${editor}"
  "${project}"
  -nullrhi
  -nosound
  -unattended
  -NoSplash
  -notraceserver
  -ddc=NoZenLocalFallback
  -NoAssetRegistryCacheWrite
  "-ini:EditorPerProjectUserSettings:[/Script/UnrealEd.EditorLoadingSavingSettings]:LoadLevelAtStartup=None"
  "-ini:Editor:[/Script/SourceControl.SourceControlPreferences]:bEnableUncontrolledChangelists=False"
  -ExecCmds=QUIT_EDITOR
)
printf "%q " "${command[@]}" > "${run_dir}/command.txt"
printf "\n" >> "${run_dir}/command.txt"
for source in carla ue; do
  source_var="${source}_dir"
  git_cmd=(git -c "safe.directory=${!source_var}" -C "${!source_var}")
  "${git_cmd[@]}" rev-parse HEAD > "${run_dir}/${source}-commit.txt"
  "${git_cmd[@]}" diff --binary HEAD > "${run_dir}/${source}-tracked.patch"
done

step=editor-startup
ulimit -c 0
set +e
"${command[@]}" 2>&1 | tee "${run_dir}/editor.log"
exit_code=${PIPESTATUS[0]}
set -e
printf "%s\n" "${exit_code}" > "${run_dir}/exit-code.txt"
step=log-validation

if [[ "${exit_code}" -ne 0 ]]; then
  echo "Editor startup failed: exit ${exit_code}; timeout and signals are failures" >&2
  exit "${exit_code}"
fi

required_markers=(
  "Engine is initialized. Leaving FEngineLoop::Init()"
  "Cmd: QUIT_EDITOR"
  "Engine exit requested (reason: UUnrealEdEngine::CloseEditor())"
)
for marker in "${required_markers[@]}"; do
  if ! grep -aFq "${marker}" "${run_dir}/editor.log"; then
    echo "Required Editor startup marker is missing: ${marker}" >&2
    exit 3
  fi
done

rejected_markers=(
  "MAP LOAD"
  "LoadDefaultMapAtStartup"
  "SIGSEGV"
  "Fatal error!"
  "Unhandled Exception:"
  "Signal 3 caught"
  "Signal 7 caught"
  "Exiting abnormally"
)
for marker in "${rejected_markers[@]}"; do
  if grep -aFq "${marker}" "${run_dir}/editor.log"; then
    echo "Forbidden Editor startup marker is present: ${marker}" >&2
    exit 3
  fi
done

step=complete
