#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-4}"
build_timeout="${CARLA_UFBXBRIDGE_BUILD_TIMEOUT:-2700}"
for value in "${jobs}" "${build_timeout}"; do
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || { echo "Build limits must be positive integers" >&2; exit 64; }
done
[[ "${#jobs}" -eq 1 && "${jobs}" -le 4 ]] || { echo "Maximum JOBS is 4" >&2; exit 64; }
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run inside native ARM64 carla-build Docker" >&2
  exit 2
fi

ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
template="${script_dir}/ue-meshbridge"
project_dir="${artifact_dir}/ue-ufbxbridge/project"
project="${project_dir}/CarlaMeshBridge.uproject"
binary="${project_dir}/Binaries/Linux/CarlaMeshBridge"
export CARLA_ASSIMP_INSTALL="${artifact_dir}/assimp-arm64/6.0.5/install"
export CARLA_UFBX_INSTALL="${CARLA_UFBX_INSTALL-${artifact_dir}/ufbx-arm64/0.23.0/install}"
ufbx_report="${CARLA_UFBX_REPORT:-${artifact_dir}/ufbx-arm64/0.23.0/last-verified-stage.json}"
assimp_report="${artifact_dir}/assimp-arm64/6.0.5/last-verified-stage.json"
scope="ufbx to UE FMeshDescription in-memory bridge; dual backend, not SDK replacement, UStaticMesh, Editor or Cook"
mkdir -p "${artifact_dir}" "${project_dir}"
run_dir="$(mktemp -d "${artifact_dir}/ue-ufbxbridge-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
ue_commit=unverified

finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 && "${step}" == complete ]] && status=PASS
  if [[ ! -s "${run_dir}/stage-report.json" ]]; then
    python3 "${script_dir}/stage_report.py" write --output "${run_dir}/stage-report.json" \
      --stage-id ue-ufbx-meshdescription-static --scope "${scope}" --exit-code "${code}" \
      --required-check build --check build FAIL --evidence build "${run_dir}/build.log" \
      --source ue "${ue_dir}" "${ue_commit}" --command bash "${BASH_SOURCE[0]}" \
      > "${run_dir}/report-error.log" 2>&1 || true
  fi
  printf "# Native UE ufbx Bridge\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Scope: %s\n" \
    "${status}" "${step}" "${code}" "${scope}" > "${run_dir}/decision.md"
  printf "%s ue-ufbxbridge artifacts=%s step=%s exit=%s\n" "${status}" "${run_dir}" "${step}" "${code}"
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
run_step prerequisite python3 "${script_dir}/stage_report.py" validate "${ufbx_report}" \
  --stage-id ufbx-fbx-static-backend --scope "ufbx static FBX backend only; not Autodesk SDK ABI, UE Editor/Cook or RPC"
run_step assimp-prerequisite python3 "${script_dir}/stage_report.py" validate "${assimp_report}" \
  --stage-id assimp-fbx-backend --scope "Assimp FBX backend only; not Autodesk SDK ABI or Unreal Editor/Cook"
run_step capture-inputs python3 "${script_dir}/check_ue_ufbxbridge.py" capture-inputs \
  --output "${run_dir}/build-inputs.json" --program "${binary}" --source-root "${template}" \
  --ufbx-install "${CARLA_UFBX_INSTALL}" --ufbx-report "${ufbx_report}" --ue-commit "${ue_commit}" \
  --assimp-library "${CARLA_ASSIMP_INSTALL}/lib/libassimp.so.6"

for entry in Source CarlaMeshBridge.uproject; do
  destination="${project_dir}/${entry}"
  if [[ -L "${destination}" ]]; then
    [[ "$(readlink "${destination}")" == "${template}/${entry}" ]] || {
      echo "Refusing unexpected project link: ${destination}" >&2; exit 2;
    }
  elif [[ -e "${destination}" ]]; then
    echo "Refusing existing project source: ${destination}" >&2; exit 2
  else
    ln -s "${template}/${entry}" "${destination}"
  fi
done
find "${template}" -type f -print0 | sort -z | xargs -0 sha256sum > "${run_dir}/source-files.sha256"
build_command=(
  timeout --kill-after=30 "${build_timeout}"
  bash "${ue_dir}/Engine/Build/BatchFiles/Linux/Build.sh"
  CarlaMeshBridge Linux Development -architecture=arm64 "-project=${project}"
  -NoUBTMakefiles -NoDumpSyms "-MaxParallelActions=${jobs}" "-Log=${run_dir}/ubt.log"
)
python3 -c "import json, sys; print(json.dumps(sys.argv[1:]))" "${build_command[@]}" > "${run_dir}/build-command.json"
run_step build "${build_command[@]}"
[[ -x "${binary}" ]] || { echo "Missing native bridge program" >&2; exit 1; }
run_step architecture file -L "${binary}"
run_step library-copy cmp "${CARLA_ASSIMP_INSTALL}/lib/libassimp.so.6" \
  "${project_dir}/Binaries/Linux/libassimp.so.6"
run_step linkage env -u LD_LIBRARY_PATH ldd -r "${binary}"
if grep -Eq "not found|undefined symbol" "${run_dir}/linkage.log"; then
  echo "Native bridge has unresolved runtime dependencies" >&2; exit 1
fi
run_step unchanged-sources sha256sum --check "${run_dir}/source-files.sha256"
run_step seal-build python3 "${script_dir}/check_ue_ufbxbridge.py" seal-build \
  --inputs "${run_dir}/build-inputs.json" --output "${run_dir}/build-manifest.json"
ulimit -c 0
run_step assimp-regression env -u LD_LIBRARY_PATH python3 "${script_dir}/check_ue_meshbridge.py" \
  --program "${binary}" --fixtures "${ue_dir}/Engine/Content/FbxEditorAutomation" \
  --run-dir "${run_dir}/assimp-regression" --ue-root "${ue_dir}" --ue-commit "${ue_commit}" \
  --assimp-report "${assimp_report}" --build-command "${run_dir}/build-command.json"
run_step validation env -u LD_LIBRARY_PATH python3 "${script_dir}/check_ue_ufbxbridge.py" run \
  --program "${binary}" --fixtures "${ue_dir}/Engine/Content/FbxEditorAutomation" \
  --run-dir "${run_dir}" --ue-root "${ue_dir}" --ue-commit "${ue_commit}" \
  --ufbx-report "${ufbx_report}" --ufbx-install "${CARLA_UFBX_INSTALL}" --assimp-report "${assimp_report}" \
  --assimp-bridge-report "${run_dir}/assimp-regression/stage-report.json" \
  --build-manifest "${run_dir}/build-manifest.json" --build-command "${run_dir}/build-command.json"
run_step report-validation python3 "${script_dir}/stage_report.py" validate "${run_dir}/stage-report.json" \
  --stage-id ue-ufbx-meshdescription-static --scope "${scope}"
step=complete
