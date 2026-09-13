#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

mode="${1:-check}"
case "${mode}" in
  check|setup) ;;
  *) echo 'Usage: ue-setup.sh [check|setup]' >&2; exit 64 ;;
esac

ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
mkdir -p "${artifact_dir}"
run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="$(mktemp -d "${artifact_dir}/ue-${mode}-${run_stamp}-XXXXXX")"
chmod 0755 "${run_dir}"
mkdir -p "${run_dir}/logs" "${run_dir}/metrics"
finished=0
step=preflight

finish() {
  local status="$1" detail="$2" code="$3"
  printf '# Unreal Engine Harness\n\n- Status: %s\n- Mode: %s\n- Step: %s\n- Timestamp UTC: %s\n- Detail: %s\n- Scope: host dependency tools only; not UnrealEditor or CARLA runtime\n' \
    "${status}" "${mode}" "${step}" "${run_stamp}" "${detail}" > "${run_dir}/decision.md"
  finished=1
  printf '%s harness test=carla-ue-%s artifacts=%s detail=%s\n' "${status}" "${mode}" "${run_dir}" "${detail}"
  exit "${code}"
}

on_exit() {
  local code=$?
  if [[ "${finished}" == 0 ]]; then
    finish FAIL "unexpected failure at ${step}, exit=${code}" "${code:-1}"
  fi
}
trap on_exit EXIT

[[ "$(uname -m)" == aarch64 || "$(uname -m)" == arm64 ]] \
  || finish BLOCKED 'native ARM64 host is required' 2
[[ -f "${ue_dir}/Setup.sh" && -d "${ue_dir}/Engine" && -e "${ue_dir}/.git" ]] \
  || finish BLOCKED 'Unreal source checkout is missing or incomplete' 2
[[ "${CARLA_UE_COMMIT:-}" =~ ^[0-9a-f]{40}$ && -n "${CARLA_UE_REF:-}" ]] \
  || finish BLOCKED 'full CARLA_UE_COMMIT and CARLA_UE_REF are required' 2

git_cmd=(git -c "safe.directory=${ue_dir}" -C "${ue_dir}")
actual_commit="$("${git_cmd[@]}" rev-parse HEAD)"
actual_branch="$("${git_cmd[@]}" symbolic-ref --short HEAD 2>/dev/null || printf detached)"
printf 'ue_commit=%s\nue_branch=%s\n' "${actual_commit}" "${actual_branch}" | tee "${run_dir}/metrics/source.txt"
[[ "${actual_commit}" == "${CARLA_UE_COMMIT}" && "${actual_branch}" == "${CARLA_UE_REF}" ]] \
  || finish FAIL 'Unreal branch or commit does not match the configured pin' 1
"${git_cmd[@]}" diff --quiet HEAD -- \
  || finish FAIL 'Unreal tracked source differs from the pinned commit' 1

run_step() {
  step="$1"
  shift
  printf 'step=%s\n' "${step}"
  local code=0
  "$@" 2>&1 | tee "${run_dir}/logs/${step}.txt" || code=$?
  [[ "${code}" == 0 ]] || finish FAIL "${step} exited ${code}" "${code}"
}

command -v dotnet >/dev/null || finish BLOCKED 'ARM64 .NET SDK is missing' 2
run_step dotnet-info dotnet --info
gitdeps="${ue_dir}/Engine/Build/BatchFiles/Linux/GitDependencies.sh"
[[ -f "${gitdeps}" ]] || finish BLOCKED 'GitDependencies wrapper is missing' 2
run_step gitdeps-help timeout 300 bash "${gitdeps}" --help
grep -q 'GitDependencies \[options\]' "${run_dir}/logs/gitdeps-help.txt" \
  || finish FAIL 'GitDependencies returned no valid help output' 1

if [[ "${mode}" == check ]]; then
  finish PASS 'GitDependencies builds and runs on this host; no engine build performed' 0
fi

export GIT_TERMINAL_PROMPT=0
export CARLA_UNREAL_ENGINE_PATH="${ue_dir}"
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="${ue_dir}"
run_step setup timeout "${CARLA_UE_SETUP_TIMEOUT:-7200}" bash "${ue_dir}/Setup.sh" --force
run_step prepare-arm64-compiler bash /opt/my-ad/scripts/carla/prepare-arm64-ue-toolchain.sh "${ue_dir}"
run_step generate-project-files timeout 600 bash "${ue_dir}/GenerateProjectFiles.sh" -Platforms=LinuxArm64
grep -E 'Some Platforms were skipped|program do not support target platform|FBX SDK not found|mismatched .SupportedTargetPlatforms' \
  "${run_dir}/logs/generate-project-files.txt" > "${run_dir}/metrics/warnings.txt" || true
if grep -Eiq 'Exec format error|Compilation failed|GenerateProjectFiles ERROR|Unhandled exception|fatal error:' \
    "${run_dir}/logs/generate-project-files.txt"; then
  finish FAIL 'GenerateProjectFiles log contains a toolchain or compilation error' 1
fi
finish PASS 'Setup and project generation completed; engine compilation is still required' 0
