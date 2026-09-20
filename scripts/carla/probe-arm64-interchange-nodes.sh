#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run inside native ARM64 carla-build Docker" >&2
  exit 2
fi
jobs="${CARLA_BUILD_JOBS:-4}"
[[ "${jobs}" =~ ^[1-4]$ ]] || { echo "CARLA_BUILD_JOBS must be 1..4" >&2; exit 64; }
ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
template="${script_dir}/ue-interchange"
project_dir="${artifact_dir}/ue-interchange-v3/project"
mode="${CARLA_INTERCHANGE_MODE:-static}"
[[ "${mode}" == static || "${mode}" == parser || "${mode}" == worker || "${mode}" == legacy ]] || { echo "CARLA_INTERCHANGE_MODE must be static, parser, worker or legacy" >&2; exit 64; }
stage_id=ue-ufbx-interchange-static
scope="ufbx to UE Interchange static nodes and mesh payloads; not translator/worker, material shading, factory assets, Editor or Cook"
prefix=interchange-nodes
export CARLA_INTERCHANGE_UFBX_STATIC=0
if [[ "${mode}" == parser || "${mode}" == worker ]]; then
  export CARLA_INTERCHANGE_UFBX_STATIC=1
  project_dir="${artifact_dir}/ue-interchange-parser-v1/project"
  prefix=interchange-parser
  stage_id=ue-ufbx-parser-static
  scope="Real FInterchangeFbxParser with static ufbx session; not worker process, factory assets, full FBX, Editor or Cook"
fi
if [[ "${mode}" == legacy ]]; then
  prefix=legacy-hierarchy
  stage_id=ue-ufbx-legacy-hierarchy
  scope="ufbx to shared Legacy factory hierarchy; not asset import, save/reimport, Editor or Cook"
fi
if [[ "${mode}" == worker ]]; then
  project_dir="${artifact_dir}/ue-interchange-worker-v1/probe"
  worker_dir="${artifact_dir}/ue-interchange-worker-v1/worker"
  prefix=interchange-worker
  stage_id=ue-ufbx-worker-static
  scope="Real InterchangeWorker static ufbx over UE command-queue TCP; not production WorkerHandler, full FBX, Editor or Cook"
fi
scene_fixture="${script_dir}/ufbx-probe/fixtures/multi-mesh.fbx"
export CARLA_ASSIMP_INSTALL="${artifact_dir}/assimp-arm64/6.0.5/install"
export CARLA_UFBX_INSTALL="${artifact_dir}/ufbx-arm64/0.23.0/install"
mkdir -p "${artifact_dir}" "${project_dir}"
run_dir="$(mktemp -d "${artifact_dir}/${prefix}-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
finish() {
  local code=$?
  if [[ ! -s "${run_dir}/stage-report.json" && -s "${run_dir}/build.log" ]]; then
    python3 "${script_dir}/stage_report.py" write \
      --output "${run_dir}/stage-report.json" \
      --stage-id "${stage_id}" --scope "${scope}" \
      --exit-code "${code}" \
      --required-check build --check build FAIL --evidence build "${run_dir}/build.log" \
      --source ue "${ue_dir}" "${ue_commit:-unverified}" \
      --command bash "${BASH_SOURCE[0]}" > "${run_dir}/report-error.log" 2>&1 || true
  fi
  if [[ "${mode}" != static ]]; then
    printf "%s artifacts=%s exit=%s scope=%s\n" "${stage_id}" "${run_dir}" "${code}" "${scope}"
  else
    printf "interchange-node-bootstrap artifacts=%s exit=%s (not FBX translation or Cook)\n" "${run_dir}" "${code}"
  fi
}
trap finish EXIT
if [[ "${mode}" == parser || "${mode}" == worker ]]; then
  python3 "${script_dir}/prepare_interchange_parser.py" --ue-root "${ue_dir}" \
    --artifact-root "${artifact_dir}/parser-source" > "${run_dir}/prepare-dir.txt"
fi
if [[ "${mode}" == worker ]]; then
  python3 "${script_dir}/prepare_interchange_worker.py" --ue-root "${ue_dir}" \
    --artifact-root "${artifact_dir}/worker-source" > "${run_dir}/worker-prepare-dir.txt"
fi
for entry in CarlaInterchangeProbe.uproject; do
  destination="${project_dir}/${entry}"
  if [[ -L "${destination}" ]]; then
    [[ "$(readlink "${destination}")" == "${template}/${entry}" ]] || {
      echo "Unexpected project link: ${destination}" >&2; exit 2;
    }
  elif [[ -e "${destination}" ]]; then
    echo "Refusing to overwrite existing project source: ${destination}" >&2; exit 2
  else
    ln -s "${template}/${entry}" "${destination}"
  fi
done
mkdir -p "${project_dir}/Source"
target_rules="${project_dir}/Source/CarlaInterchangeProbe.Target.cs"
if [[ -L "${target_rules}" ]]; then
  [[ "$(readlink "${target_rules}")" == "${template}/Source/CarlaInterchangeProbe.Target.cs" ]] || {
    echo "Unexpected target rules link: ${target_rules}" >&2; exit 2;
  }
elif [[ -e "${target_rules}" ]]; then
  echo "Refusing to overwrite target rules: ${target_rules}" >&2; exit 2
else
  ln -s "${template}/Source/CarlaInterchangeProbe.Target.cs" "${target_rules}"
fi
for module in CarlaInterchangeProbe CarlaUfbxInterchange CarlaUfbxLegacy CarlaUfbxMesh CarlaAssimpMesh; do
  link="${project_dir}/Source/${module}"
  if [[ "${module}" == CarlaInterchangeProbe || "${module}" == CarlaUfbxInterchange || "${module}" == CarlaUfbxLegacy ]]; then
    target="${template}/Source/${module}"
  else
    target="${script_dir}/ue-meshbridge/Source/${module}"
  fi
  if [[ -L "${link}" ]]; then
    [[ "$(readlink "${link}")" == "${target}" ]] || { echo "Unexpected source link: ${link}" >&2; exit 2; }
  elif [[ -e "${link}" ]]; then
    echo "Refusing to overwrite existing source link: ${link}" >&2; exit 2
  else
    ln -s "${target}" "${link}"
  fi
done
if [[ "${mode}" == worker ]]; then
  mkdir -p "${worker_dir}/Source"
  for entry in CarlaInterchangeWorker.uproject Source/CarlaInterchangeWorker.Target.cs; do
    link="${worker_dir}/${entry}"
    target="${template}/${entry}"
    if [[ -L "${link}" ]]; then
      [[ "$(readlink "${link}")" == "${target}" ]] || { echo "Conflicting Worker project link" >&2; exit 2; }
    elif [[ -e "${link}" ]]; then
      echo "Refusing to overwrite Worker project source" >&2; exit 2
    else
      ln -s "${target}" "${link}"
    fi
  done
  for module in CarlaUfbxInterchange CarlaUfbxMesh CarlaAssimpMesh; do
    link="${worker_dir}/Source/${module}"
    target="${script_dir}/ue-meshbridge/Source/${module}"
    [[ "${module}" != CarlaUfbxInterchange ]] || target="${template}/Source/${module}"
    if [[ -L "${link}" ]]; then
      [[ "$(readlink "${link}")" == "${target}" ]] || { echo "Conflicting Worker module link" >&2; exit 2; }
    elif [[ -e "${link}" ]]; then
      echo "Refusing to overwrite Worker module source" >&2; exit 2
    else
      ln -s "${target}" "${link}"
    fi
  done
fi
find "${template}" "${script_dir}/ue-meshbridge/Source/CarlaUfbxMesh" \
  "${script_dir}/ue-meshbridge/Source/CarlaAssimpMesh" -type f -print0 \
  | sort -z | xargs -0 sha256sum > "${run_dir}/source-files.sha256"
sha256sum "${scene_fixture}" > "${run_dir}/scene-fixture.sha256"
sha256sum "${scene_fixture}" "${BASH_SOURCE[0]}" "${script_dir}/check_ue_interchange.py" \
  "${script_dir}/stage_report.py" >> "${run_dir}/source-files.sha256"
if [[ "${mode}" == parser || "${mode}" == worker ]]; then
  find "${script_dir}/ue-parser" \
    "${ue_dir}/Engine/Plugins/Interchange/Runtime/Source/Parsers/Fbx" -type f -print0 \
    | sort -z | xargs -0 sha256sum >> "${run_dir}/source-files.sha256"
  sha256sum "${script_dir}/prepare_interchange_parser.py" "${script_dir}/check_ue_parser.py" \
    "${ue_dir}/Engine/Content/FbxEditorAutomation/AnimatedCharacter.fbx" \
    "${ue_dir}/Engine/Content/FbxEditorAutomation/MorphTargets.fbx" >> "${run_dir}/source-files.sha256"
fi
for header in SceneImportNodeInfo.h SceneImportHierarchy.h; do
  source="${ue_dir}/Engine/Source/Editor/UnrealEd/Public/ImportUtils/${header}"
  sha256sum "${source}" >> "${run_dir}/source-files.sha256"
  cp "${source}" "${run_dir}/${header}"
done
if [[ "${mode}" == legacy ]]; then
  sha256sum "${script_dir}/check_legacy_hierarchy.py" \
    "${script_dir}/ufbx-probe/fixtures/hierarchy-geometry.fbx" >> "${run_dir}/source-files.sha256"
fi
if [[ "${mode}" == worker ]]; then
  find "${ue_dir}/Engine/Source/Programs/InterchangeWorker" -type f -print0 \
    | sort -z | xargs -0 sha256sum >> "${run_dir}/source-files.sha256"
  sha256sum "${script_dir}/prepare_interchange_worker.py" "${script_dir}/check_ue_worker.py" \
    >> "${run_dir}/source-files.sha256"
fi
git -c "safe.directory=${ue_dir}" -C "${ue_dir}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
git -c "safe.directory=${ue_dir}" -C "${ue_dir}" diff --binary HEAD > "${run_dir}/ue-tracked.patch"
if [[ "${mode}" == worker ]]; then
  worker_command=(timeout --kill-after=30 2700 bash "${ue_dir}/Engine/Build/BatchFiles/Linux/Build.sh"
    CarlaInterchangeWorker Linux Development -architecture=arm64
    "-project=${worker_dir}/CarlaInterchangeWorker.uproject" -NoUBTMakefiles -NoDumpSyms
    "-MaxParallelActions=${jobs}" "-Log=${run_dir}/worker-ubt.log")
  python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "${worker_command[@]}" > "${run_dir}/worker-build-command.json"
  if ! "${worker_command[@]}" > "${run_dir}/build.log" 2>&1; then
    tail -n 80 "${run_dir}/build.log"; exit 1
  fi
  cp "${run_dir}/build.log" "${run_dir}/worker-build.log"
  worker_binary="${worker_dir}/Binaries/Linux/CarlaInterchangeWorker"
  file -L "${worker_binary}" > "${run_dir}/worker-architecture.txt"
  grep -q "ARM aarch64" "${run_dir}/worker-architecture.txt"
  sha256sum "${worker_binary}" > "${run_dir}/worker-binary.sha256"
  ldd -r "${worker_binary}" > "${run_dir}/worker-linkage.log" 2>&1
  if grep -Eq "not found|undefined symbol|libfbxsdk" "${run_dir}/worker-linkage.log"; then
    echo "Worker linkage is unresolved or uses Autodesk FBX" >&2; exit 1
  fi
fi
command=(
  timeout --kill-after=30 2700 bash "${ue_dir}/Engine/Build/BatchFiles/Linux/Build.sh"
  CarlaInterchangeProbe Linux Development -architecture=arm64
  "-project=${project_dir}/CarlaInterchangeProbe.uproject" -NoUBTMakefiles -NoDumpSyms
  "-MaxParallelActions=${jobs}" "-Log=${run_dir}/ubt.log"
)
python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "${command[@]}" > "${run_dir}/build-command.json"
if ! "${command[@]}" > "${run_dir}/build.log" 2>&1; then
  tail -n 80 "${run_dir}/build.log"
  exit 1
fi
binary="${project_dir}/Binaries/Linux/CarlaInterchangeProbe"
file -L "${binary}" > "${run_dir}/architecture.txt"
grep -q "ARM aarch64" "${run_dir}/architecture.txt"
sha256sum "${binary}" > "${run_dir}/binary.sha256"
sha256sum --check "${run_dir}/source-files.sha256"
ulimit -c 0
if [[ "${mode}" == legacy ]]; then
  ldd -r "${binary}" > "${run_dir}/runtime-linkage.log" 2>&1
  if grep -Eq "not found|undefined symbol|libfbxsdk" "${run_dir}/runtime-linkage.log"; then
    echo "Legacy hierarchy linkage is unresolved or still uses Autodesk FBX SDK" >&2
    exit 1
  fi
  set +e
  timeout --kill-after=10 120 "${binary}" -legacy-hierarchy-test \
    "-input=${scene_fixture}" "-output=${run_dir}/native.json" "-result-dir=${run_dir}/outputs" \
    > "${run_dir}/native.log" 2>&1
  process_code=$?
  set -e
  if ! sha256sum --check "${run_dir}/source-files.sha256" >> "${run_dir}/native.log" 2>&1; then
    process_code=125
  fi
  python3 "${script_dir}/check_legacy_hierarchy.py" --program "${binary}" --input "${scene_fixture}" \
    --run-dir "${run_dir}" --ue-root "${ue_dir}" --exit-code "${process_code}" \
    --ufbx-report "${artifact_dir}/ufbx-arm64/0.23.0/last-verified-stage.json"
  exit $?
fi
if [[ "${mode}" == worker ]]; then
  set +e
  timeout --kill-after=10 150 "${binary}" -native-worker-test "-worker=${worker_binary}" \
    "-input=${scene_fixture}" "-output=${run_dir}/native.json" "-result-dir=${run_dir}/outputs" \
    > "${run_dir}/native.log" 2>&1
  process_code=$?
  set -e
  if ! sha256sum --check "${run_dir}/source-files.sha256" >> "${run_dir}/native.log" 2>&1; then
    process_code=125
  fi
  python3 "${script_dir}/check_ue_worker.py" --program "${binary}" --worker "${worker_binary}" \
    --input "${scene_fixture}" --run-dir "${run_dir}" --ue-root "${ue_dir}" --exit-code "${process_code}" \
    --ufbx-report "${artifact_dir}/ufbx-arm64/0.23.0/last-verified-stage.json"
  exit $?
fi
if [[ "${mode}" == parser ]]; then
  ldd -r "${binary}" > "${run_dir}/runtime-linkage.log" 2>&1
  if grep -Eq "not found|undefined symbol|libfbxsdk" "${run_dir}/runtime-linkage.log"; then
    echo "Parser linkage is unresolved or still uses Autodesk FBX SDK" >&2
    exit 1
  fi
  set +e
  timeout --kill-after=10 120 "${binary}" -native-parser-test \
    "-input=${scene_fixture}" "-fixture-root=${ue_dir}/Engine/Content/FbxEditorAutomation" \
    "-output=${run_dir}/native.json" "-result-dir=${run_dir}/outputs" > "${run_dir}/native.log" 2>&1
  process_code=$?
  set -e
  if ! sha256sum --check "${run_dir}/source-files.sha256" >> "${run_dir}/native.log" 2>&1; then
    process_code=125
  fi
  python3 "${script_dir}/check_ue_parser.py" --program "${binary}" --input "${scene_fixture}" \
    --run-dir "${run_dir}" --ue-root "${ue_dir}" --exit-code "${process_code}" \
    --ufbx-report "${artifact_dir}/ufbx-arm64/0.23.0/last-verified-stage.json"
  exit $?
fi
set +e
timeout --kill-after=10 120 "${binary}" -scene-test \
  "-input=${scene_fixture}" \
  "-output=${run_dir}/scene.json" "-result-dir=${run_dir}/outputs" \
  > "${run_dir}/bootstrap.log" 2>&1
process_code=$?
set -e
if ! sha256sum --check "${run_dir}/source-files.sha256" >> "${run_dir}/bootstrap.log" 2>&1; then
  if [[ "${process_code}" == 0 ]]; then
    process_code=125
  fi
fi
python3 "${script_dir}/check_ue_interchange.py" \
  --program "${binary}" \
  --input "${scene_fixture}" \
  --run-dir "${run_dir}" --ue-root "${ue_dir}" \
  --ufbx-report "${artifact_dir}/ufbx-arm64/0.23.0/last-verified-stage.json" \
  --runner "${BASH_SOURCE[0]}" \
  --expected-meshes 2 \
  --exit-code "${process_code}"
python3 "${script_dir}/stage_report.py" validate \
  "${run_dir}/stage-report.json" \
  --stage-id ue-ufbx-interchange-static \
  --scope "ufbx to UE Interchange static nodes and mesh payloads; not translator/worker, material shading, factory assets, Editor or Cook"
