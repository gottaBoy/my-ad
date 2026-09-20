#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-4}"
build_timeout="${CARLA_STATICMESH_BUILD_TIMEOUT:-1800}"
[[ "${jobs}" =~ ^[1-4]$ && "${build_timeout}" =~ ^[1-9][0-9]*$ ]] || {
  echo "Jobs must be 1..4 and build timeout must be positive" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run in native ARM64 Docker" >&2; exit 2;
}
ue="${CARLA_UE_DIR:-/workspace/unreal-engine}"
artifacts="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
scripts="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
template="${scripts}/ue-staticmesh"
project_dir="${artifacts}/ue-staticmesh-v1/project"
mkdir -p "${project_dir}/Source"
run="$(mktemp -d "${artifacts}/staticmesh-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run}"
export XDG_CONFIG_HOME="${run}/config"
mkdir -p "${XDG_CONFIG_HOME}/Unreal Engine/UnrealBuildTool"
cp "${template}/BuildConfiguration.xml" "${XDG_CONFIG_HOME}/Unreal Engine/UnrealBuildTool/BuildConfiguration.xml"
scope="Transient UStaticMesh fast-build CPU buffers; not saved assets, Editor/Cook or GPU rendering"
step=preflight
finish() {
  code=$?
  printf "staticmesh artifacts=%s step=%s exit=%s scope=%s\n" "${run}" "${step}" "${code}" "${scope}"
  if [[ ! -f "${run}/stage-report.json" ]]; then
    python3 -B "${scripts}/stage_report.py" write --output "${run}/stage-report.json" \
      --stage-id ue-ufbx-runtime-staticmesh --scope "${scope}" --exit-code "${code}" \
      --required-check complete --check complete FAIL --evidence complete "${run}/${step}.log" \
      --source ue "${ue}" "${revision:-unverified}" --command bash "${BASH_SOURCE[0]}" \
      > "${run}/report-error.log" 2>&1 || true
  fi
}
trap finish EXIT
revision="$(git -c "safe.directory=${ue}" -C "${ue}" rev-parse HEAD)"
printf "%s\n" "${revision}" > "${run}/ue-commit.txt"
git -c "safe.directory=${ue}" -C "${ue}" diff --binary HEAD > "${run}/ue-tracked.patch"
export CARLA_ASSIMP_INSTALL="${artifacts}/assimp-arm64/6.0.5/install"
export CARLA_UFBX_INSTALL="${artifacts}/ufbx-arm64/0.23.0/install"
export CARLA_INTERCHANGE_UFBX_STATIC=0
export CARLA_ARM64_FBX_HEADERS_ONLY=0
export CARLA_DISABLE_ISPC=1
for entry in CarlaStaticMeshProbe.uproject Source/CarlaStaticMeshProbe.Target.cs Source/CarlaStaticMeshProbe; do
  link="${project_dir}/${entry}"
  target="${template}/${entry}"
  if [[ -L "${link}" ]]; then
    [[ "$(readlink "${link}")" == "${target}" ]] || { echo "Unexpected link ${link}" >&2; exit 2; }
  elif [[ -e "${link}" ]]; then
    echo "Refusing to overwrite ${link}" >&2; exit 2
  else
    ln -s "${target}" "${link}"
  fi
done
for module in CarlaAssimpMesh CarlaUfbxMesh CarlaUfbxLegacy; do
  target="${scripts}/ue-meshbridge/Source/${module}"
  [[ "${module}" != CarlaUfbxLegacy ]] || target="${scripts}/ue-interchange/Source/${module}"
  link="${project_dir}/Source/${module}"
  if [[ -L "${link}" ]]; then
    [[ "$(readlink "${link}")" == "${target}" ]] || { echo "Unexpected link ${link}" >&2; exit 2; }
  elif [[ -e "${link}" ]]; then
    echo "Refusing to overwrite ${link}" >&2; exit 2
  else
    ln -s "${target}" "${link}"
  fi
done
find "${template}" "${scripts}/ue-meshbridge/Source/CarlaUfbxMesh" \
  "${scripts}/ue-meshbridge/Source/CarlaAssimpMesh" "${scripts}/ue-interchange/Source/CarlaUfbxLegacy" \
  -type f -print0 | sort -z | xargs -0 sha256sum > "${run}/source-files.sha256"
for header in SceneImportNodeInfo.h SceneImportHierarchy.h; do
  sha256sum "${ue}/Engine/Source/Editor/UnrealEd/Public/ImportUtils/${header}" >> "${run}/source-files.sha256"
done
sha256sum "${BASH_SOURCE[0]}" "${scripts}/ufbx-probe/fixtures/multi-mesh.fbx" >> "${run}/source-files.sha256"
base=(bash "${ue}/Engine/Build/BatchFiles/Linux/Build.sh" CarlaStaticMeshProbe Linux Development
  -architecture=arm64 "-project=${project_dir}/CarlaStaticMeshProbe.uproject" -NoUBTMakefiles -NoDumpSyms
  -buildubt -ForceRulesCompile "-MaxParallelActions=${jobs}")
step=graph
timeout --kill-after=10 120 "${base[@]}" -Mode=JsonExport "-OutputFile=${run}/target.json" > "${run}/graph.log" 2>&1
step=build
printf "%q " "${base[@]}" > "${run}/build-command.txt"
if ! timeout --kill-after=30 "${build_timeout}" "${base[@]}" "-Log=${run}/ubt.log" > "${run}/build.log" 2>&1; then
  tail -80 "${run}/build.log"; exit 1
fi
binary="${project_dir}/Binaries/Linux/CarlaStaticMeshProbe"
file "${binary}" > "${run}/architecture.log"
grep -q 'ARM aarch64' "${run}/architecture.log"
env -u LD_LIBRARY_PATH -u LD_PRELOAD ldd -r "${binary}" > "${run}/linkage.log" 2>&1
if grep -Eq 'not found|undefined symbol|libfbxsdk' "${run}/linkage.log"; then exit 1; fi
printf '{"graph":0,"build":0,"native":null}\n' > "${run}/processes.json"
layout="${run}/layout"
staged_project="${layout}/CarlaStaticMesh"
staged_bin="${staged_project}/Binaries/Linux"
mkdir -p "${staged_bin}" "${layout}/Engine/Saved"
cp --reflink=auto "${binary}" "${staged_bin}/CarlaStaticMeshProbe"
cp "${project_dir}/CarlaStaticMeshProbe.uproject" "${staged_project}/"
cp "${ue}/Engine/Binaries/Linux/CarlaStaticMeshProbe.target" "${staged_bin}/"
for directory in Binaries Build Config Content Platforms Plugins Shaders; do
  if [[ -d "${ue}/Engine/${directory}" ]]; then
    ln -s "${ue}/Engine/${directory}" "${layout}/Engine/${directory}"
  fi
done
find "${layout}" -maxdepth 4 -printf '%y %p -> %l\n' > "${run}/layout.txt"
staged_binary="${staged_bin}/CarlaStaticMeshProbe"
step=native
ulimit -c 0
set +e
timeout --kill-after=10 120 "${staged_binary}" -nullrhi -unattended -nosound -notraceserver \
  -NoSplash -stdout -FullStdOutLogOutput -AllowStdOutLogVerbosity \
  "-abslog=${run}/unreal.log" "-project=${staged_project}/CarlaStaticMeshProbe.uproject" \
  "-input=${scripts}/ufbx-probe/fixtures/multi-mesh.fbx" \
  "-output=${run}/native.json" > "${run}/native.log" 2>&1
process_code=$?
set -e
python3 -c 'import json,sys; print(json.dumps({"graph":0,"build":0,"native":int(sys.argv[1])}))' "${process_code}" > "${run}/processes.json"
sha256sum --check "${run}/source-files.sha256" > "${run}/source-verification.log"
step=validation
python3 "${scripts}/check_staticmesh.py" --program "${staged_binary}" \
  --input "${scripts}/ufbx-probe/fixtures/multi-mesh.fbx" --run-dir "${run}" \
  --ue-root "${ue}" --exit-code "${process_code}" \
  --ufbx-report "${artifacts}/ufbx-arm64/0.23.0/last-verified-stage.json"
