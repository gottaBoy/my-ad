#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-8}"
build_timeout="${CARLA_MESHBRIDGE_BUILD_TIMEOUT:-2700}"
for value in "${jobs}" "${build_timeout}"; do
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || { echo "Build jobs and timeout must be positive integers" >&2; exit 64; }
done
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi

ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
template="${script_dir}/ue-meshbridge"
project_dir="${artifact_dir}/ue-meshbridge/project"
project="${project_dir}/CarlaMeshBridge.uproject"
export CARLA_ASSIMP_INSTALL="${artifact_dir}/assimp-arm64/6.0.5/install"
assimp_report="${artifact_dir}/assimp-arm64/6.0.5/last-verified-stage.json"
scope="UE FMeshDescription in-memory bridge; not UStaticMesh, Editor or Cook"
mkdir -p "${artifact_dir}" "${project_dir}"
run_dir="$(mktemp -d "${artifact_dir}/ue-meshbridge-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
ue_commit=unverified

finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 && "${step}" == complete ]] && status=PASS
  if [[ ! -s "${run_dir}/stage-report.json" ]]; then
    python3 "${script_dir}/stage_report.py" write --output "${run_dir}/stage-report.json" \
      --stage-id ue-meshdescription-static --scope "${scope}" --exit-code "${code}" \
      --required-check build --check build FAIL --evidence build "${run_dir}/build.log" \
      --source ue "${ue_dir}" "${ue_commit}" --command bash "${BASH_SOURCE[0]}" \
      > "${run_dir}/report-error.log" 2>&1 || true
  fi
  printf "# Native UE Static Mesh Bridge\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Scope: %s\n" \
    "${status}" "${step}" "${code}" "${scope}" > "${run_dir}/decision.md"
  printf "%s ue-meshbridge artifacts=%s step=%s exit=%s\n" "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

run_step() {
  step="$1"
  shift
  printf "step=%s\n" "${step}"
  printf "%q " "$@" > "${run_dir}/${step}.command.txt"
  printf "\n" >> "${run_dir}/${step}.command.txt"
  if "$@" > "${run_dir}/${step}.log" 2>&1; then
    tail -n 12 "${run_dir}/${step}.log"
  else
    local code=$?
    tail -n 80 "${run_dir}/${step}.log"
    return "${code}"
  fi
}

git_cmd=(git -c "safe.directory=${ue_dir}" -C "${ue_dir}")
ue_commit="$("${git_cmd[@]}" rev-parse HEAD)"
printf "%s\n" "${ue_commit}" > "${run_dir}/ue-commit.txt"
"${git_cmd[@]}" diff --binary HEAD > "${run_dir}/ue-tracked.patch"
run_step prerequisite python3 "${script_dir}/stage_report.py" validate "${assimp_report}" \
  --stage-id assimp-fbx-backend \
  --scope "Assimp FBX backend only; not Autodesk SDK ABI or Unreal Editor/Cook"

for entry in Source CarlaMeshBridge.uproject; do
  destination="${project_dir}/${entry}"
  if [[ -L "${destination}" ]]; then
    [[ "$(readlink "${destination}")" == "${template}/${entry}" ]] || {
      echo "Refusing to replace an unexpected project link: ${destination}" >&2; exit 2;
    }
  elif [[ -e "${destination}" ]]; then
    echo "Refusing to replace existing project source: ${destination}" >&2
    exit 2
  else
    ln -s "${template}/${entry}" "${destination}"
  fi
done
find "${template}" -type f -print0 | sort -z | xargs -0 sha256sum > "${run_dir}/source-files.sha256"
sha256sum "${BASH_SOURCE[0]}" "${script_dir}/stage_report.py" > "${run_dir}/scripts.sha256"
build_command=(
  timeout --kill-after=30 "${build_timeout}"
  bash "${ue_dir}/Engine/Build/BatchFiles/Linux/Build.sh"
  CarlaMeshBridge Linux Development -architecture=arm64 "-project=${project}"
  -NoUBTMakefiles -NoDumpSyms "-MaxParallelActions=${jobs}" "-Log=${run_dir}/ubt.log"
)
python3 -c "import json, sys; print(json.dumps(sys.argv[1:]))" "${build_command[@]}" > "${run_dir}/build-command.json"
run_step build "${build_command[@]}"

binary="${project_dir}/Binaries/Linux/CarlaMeshBridge"
[[ -x "${binary}" ]] || { echo "Native UE program was not produced: ${binary}" >&2; exit 1; }
step=architecture
file -L "${binary}" | tee "${run_dir}/architecture.txt"
grep -q "ARM aarch64" "${run_dir}/architecture.txt"
sha256sum "${binary}" "${project_dir}/Binaries/Linux/libassimp.so.6" > "${run_dir}/binaries.sha256"
run_step library-copy cmp "${CARLA_ASSIMP_INSTALL}/lib/libassimp.so.6" \
  "${project_dir}/Binaries/Linux/libassimp.so.6"
run_step linkage env -u LD_LIBRARY_PATH ldd -r "${binary}"
if grep -Eq "not found|undefined symbol" "${run_dir}/linkage.log"; then
  echo "UE mesh bridge has unresolved runtime dependencies" >&2
  exit 1
fi
run_step unchanged-sources sha256sum --check "${run_dir}/source-files.sha256"
ulimit -c 0
run_step validation env -u LD_LIBRARY_PATH python3 "${script_dir}/check_ue_meshbridge.py" \
  --program "${binary}" --fixtures "${ue_dir}/Engine/Content/FbxEditorAutomation" \
  --run-dir "${run_dir}" --ue-root "${ue_dir}" --ue-commit "${ue_commit}" \
  --assimp-report "${assimp_report}" --build-command "${run_dir}/build-command.json"
run_step report-validation python3 "${script_dir}/stage_report.py" validate "${run_dir}/stage-report.json" \
  --stage-id ue-meshdescription-static --scope "${scope}"
step=complete
