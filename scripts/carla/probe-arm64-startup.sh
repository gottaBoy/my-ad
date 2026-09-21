#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

startup_timeout="${CARLA_STARTUP_TIMEOUT:-60}"
[[ "${startup_timeout}" =~ ^[1-9][0-9]*$ ]] || {
  echo "CARLA_STARTUP_TIMEOUT must be positive" >&2
  exit 64
}
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-dev container" >&2
  exit 2
fi

ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
carla_dir="${CARLA_SOURCE_DIR:-/workspace/carla}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
project_dir="${carla_dir}/Unreal/CarlaUnreal"
binary="${project_dir}/Binaries/LinuxArm64/CarlaUnreal"
mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/startup-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
status=BLOCKED

finish() {
  local code=$?
  printf "# ARM64 CarlaUnreal Startup Probe\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Scope: source-layout startup with NullRHI, not a cooked package or RPC/Vulkan/sensor validation\n" \
    "${status}" "${step}" "${code}" > "${run_dir}/decision.md"
  printf "%s startup artifacts=%s step=%s exit=%s\n" "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

[[ -x "${binary}" ]] || { echo "CarlaUnreal executable is missing: ${binary}" >&2; exit 2; }
[[ -d "${ue_dir}/Engine/Content/Internationalization/icudt64l" ]] || {
  echo "Unreal ICU 64 data is missing" >&2
  exit 2
}
file -L "${binary}" | tee "${run_dir}/binary.txt"
grep -q "ARM aarch64" "${run_dir}/binary.txt"
sha256sum "${binary}" > "${run_dir}/binary.sha256"
env -u LD_LIBRARY_PATH ldd -r "${binary}" > "${run_dir}/linkage.txt" 2>&1
if grep -Eq "not found|undefined symbol" "${run_dir}/linkage.txt"; then
  echo "CarlaUnreal has unresolved dynamic dependencies" >&2
  exit 2
fi
for source in carla ue; do
  source_var="${source}_dir"
  git_cmd=(git -c "safe.directory=${!source_var}" -C "${!source_var}")
  "${git_cmd[@]}" rev-parse HEAD > "${run_dir}/${source}-commit.txt"
  "${git_cmd[@]}" diff --binary HEAD > "${run_dir}/${source}-tracked.patch"
done

step=layout
layout="${run_dir}/layout"
staged_project="${layout}/CarlaUnreal"
staged_bin="${staged_project}/Binaries/LinuxArm64"
mkdir -p "${staged_bin}"
# Copy the binary so /proc/self/exe resolves inside the diagnostic layout.
cp --reflink=auto "${binary}" "${staged_bin}/CarlaUnreal"
cp "${project_dir}/Binaries/LinuxArm64/CarlaUnreal.target" "${staged_bin}/"
cp "${project_dir}/CarlaUnreal.uproject" "${staged_project}/"
for directory in Config Content Plugins; do
  ln -s "${project_dir}/${directory}" "${staged_project}/${directory}"
done
mkdir -p "${layout}/Engine/Saved"
for directory in Binaries Build Config Content Platforms Plugins Shaders; do
  if [[ -d "${ue_dir}/Engine/${directory}" ]]; then
    ln -s "${ue_dir}/Engine/${directory}" "${layout}/Engine/${directory}"
  fi
done
find "${layout}" -maxdepth 4 -printf "%y %p -> %l\n" > "${run_dir}/layout.txt"

step=startup
command=(
  timeout --kill-after=10 "${startup_timeout}"
  "${staged_bin}/CarlaUnreal"
  -nullrhi -nosound -unattended -NoSplash -notraceserver -traceautostart=0
  -stdout -FullStdOutLogOutput -AllowStdOutLogVerbosity
  "-abslog=${run_dir}/unreal.log"
  "-ExecCmds=carla.AllowEditorContentInServerBuilds 1,quit"
)
printf "%q " "${command[@]}" > "${run_dir}/command.txt"
printf "\n" >> "${run_dir}/command.txt"
ulimit -c 0
cd "${staged_bin}"
set +e
"${command[@]}" > "${run_dir}/startup.log" 2>&1
code=$?
set -e
tail -n 60 "${run_dir}/startup.log"
printf "%s\n" "${code}" > "${run_dir}/startup-exit-code.txt"
[[ "${code}" == 0 ]] || exit "${code}"
status=OBSERVED
step=complete
