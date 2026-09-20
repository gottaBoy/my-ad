#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-4}"
build_timeout="${CARLA_ASSET_BUILD_TIMEOUT:-1800}"
[[ "${jobs}" =~ ^[1-4]$ && "${build_timeout}" =~ ^[1-9][0-9]*$ ]] || {
  echo "Jobs must be 1..4 and build timeout must be positive" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run in native ARM64 Docker" >&2; exit 2;
}
ue="${CARLA_UE_DIR:-/workspace/unreal-engine}"
artifacts="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
scripts="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
template="${scripts}/ue-asset"
project_dir="${artifacts}/ue-asset-v1/project"
mkdir -p "${project_dir}/Source"
run="$(mktemp -d "${artifacts}/asset-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run}"
export XDG_CONFIG_HOME="${run}/config"
mkdir -p "${XDG_CONFIG_HOME}/Unreal Engine/UnrealBuildTool"
cp "${template}/BuildConfiguration.xml" "${XDG_CONFIG_HOME}/Unreal Engine/UnrealBuildTool/BuildConfiguration.xml"
scope="Saved and reloaded uncooked UStaticMesh assets; not Editor reimport, Cook or GPU rendering"
step=preflight
finish() {
  code=$?
  printf "asset artifacts=%s step=%s exit=%s scope=%s\n" "${run}" "${step}" "${code}" "${scope}"
  if [[ ! -f "${run}/stage-report.json" ]]; then
    python3 -B "${scripts}/stage_report.py" write --output "${run}/stage-report.json" \
      --stage-id ue-ufbx-asset-roundtrip --scope "${scope}" --exit-code "${code}" \
      --required-check complete --check complete FAIL --evidence complete "${run}/${step}.log" \
      --source ue "${ue}" "${revision:-unverified}" --command bash "${BASH_SOURCE[0]}" \
      > "${run}/report-error.log" 2>&1 || true
  fi
}
trap finish EXIT
revision="$(git -c "safe.directory=${ue}" -C "${ue}" rev-parse HEAD)"
printf "%s\n" "${revision}" > "${run}/ue-commit.txt"
git -c "safe.directory=${ue}" -C "${ue}" diff --binary HEAD > "${run}/ue-tracked.patch"
step=engine-patches
engine_git=(git -c "safe.directory=${ue}" -C "${ue}")

# Apply a multi-file patch idempotently, one file at a time. Files already
# patched (or hand-corrected to an equivalent state) are skipped; files that
# still need the change are applied. A file failing both the forward and the
# reverse check is a hard error so divergence is never silent.
apply_patch_idempotent() {
  local patch_file="$1" log_file="$2" label="$3"
  local file failed=0
  while IFS= read -r file; do
    if "${engine_git[@]}" apply --reverse --check --include="${file}" "${patch_file}" >> "${log_file}" 2>&1; then
      printf "%s already applied: %s\n" "${label}" "${file}" >> "${log_file}"
    elif "${engine_git[@]}" apply --check --include="${file}" "${patch_file}" >> "${log_file}" 2>&1; then
      "${engine_git[@]}" apply --include="${file}" "${patch_file}" >> "${log_file}" 2>&1
      printf "%s applied: %s\n" "${label}" "${file}" >> "${log_file}"
    else
      printf "%s FAILED (neither applies): %s\n" "${label}" "${file}" >> "${log_file}"
      failed=1
    fi
  done < <(grep -E '^\+\+\+ b/' "${patch_file}" | sed 's#^+++ b/##')
  cp "${patch_file}" "${run}/"
  return "${failed}"
}

animgraph_patch="${scripts}/patches/animgraph-editoronly.patch"
if "${engine_git[@]}" apply --reverse --check "${animgraph_patch}" > "${run}/animgraph-patch-check.log" 2>&1; then
  printf "AnimGraphRuntime editor-only patch is already applied\n" >> "${run}/animgraph-patch-check.log"
else
  "${engine_git[@]}" apply --check "${animgraph_patch}" >> "${run}/animgraph-patch-check.log" 2>&1
  "${engine_git[@]}" apply "${animgraph_patch}"
fi
cp "${animgraph_patch}" "${run}/"
reverb_patch="${scripts}/patches/engine-editoronly-override.patch"
if "${engine_git[@]}" apply --reverse --check "${reverb_patch}" > "${run}/reverb-patch-check.log" 2>&1; then
  printf "ReverbEffect editor-only override patch is already applied\n" >> "${run}/reverb-patch-check.log"
else
  "${engine_git[@]}" apply --check "${reverb_patch}" >> "${run}/reverb-patch-check.log" 2>&1
  "${engine_git[@]}" apply "${reverb_patch}"
fi
cp "${reverb_patch}" "${run}/"
worldpartition_patch="${scripts}/patches/worldpartition-editoronly.patch"
cinematic_patch="${scripts}/patches/cinematic-moviescene-editoronly.patch"
instanced_patch="${scripts}/patches/instanced-placement-editor-boundary.patch"
if "${engine_git[@]}" apply --reverse --check "${cinematic_patch}" > "${run}/cinematic-patch-check.log" 2>&1; then
  printf "CinematicCamera and MovieScene editor-only patch is already applied\n" >> "${run}/cinematic-patch-check.log"
else
  "${engine_git[@]}" apply "${cinematic_patch}"
fi
cp "${cinematic_patch}" "${run}/"
if "${engine_git[@]}" apply --reverse --check "${instanced_patch}" > "${run}/instanced-patch-check.log" 2>&1; then
  printf "Instanced placement editor API boundary patch is already applied\n" >> "${run}/instanced-patch-check.log"
else
  "${engine_git[@]}" apply --check "${instanced_patch}" >> "${run}/instanced-patch-check.log" 2>&1
  "${engine_git[@]}" apply "${instanced_patch}"
fi
cp "${instanced_patch}" "${run}/"
if "${engine_git[@]}" apply --reverse --check "${worldpartition_patch}" > "${run}/worldpartition-patch-check.log" 2>&1; then
  printf "WorldPartition editor-only patch is already applied\n" >> "${run}/worldpartition-patch-check.log"
else
  "${engine_git[@]}" apply --check "${worldpartition_patch}" >> "${run}/worldpartition-patch-check.log" 2>&1
  "${engine_git[@]}" apply "${worldpartition_patch}"
fi
cp "${worldpartition_patch}" "${run}/"
animation_patch="${scripts}/patches/animation-editoronly.patch"
: > "${run}/animation-patch-check.log"
apply_patch_idempotent "${animation_patch}" "${run}/animation-patch-check.log" "Animation editor-only data patch"
seqbase_patch="${scripts}/patches/animation-sequencebase-editor-boundary.patch"
: > "${run}/seqbase-patch-check.log"
apply_patch_idempotent "${seqbase_patch}" "${run}/seqbase-patch-check.log" "AnimSequenceBase/Helpers editor-only data patch"
material_patch="${scripts}/patches/material-editor-boundary.patch"
: > "${run}/material-patch-check.log"
apply_patch_idempotent "${material_patch}" "${run}/material-patch-check.log" "Material editor API boundary patch"
staticmesh_patch="${scripts}/patches/staticmesh-editor-boundary.patch"
: > "${run}/staticmesh-patch-check.log"
apply_patch_idempotent "${staticmesh_patch}" "${run}/staticmesh-patch-check.log" "StaticMesh.cpp editor boundary patch"
skeletalimport_patch="${scripts}/patches/skeletalmesh-importdata-editor-boundary.patch"
: > "${run}/skeletalimport-patch-check.log"
apply_patch_idempotent "${skeletalimport_patch}" "${run}/skeletalimport-patch-check.log" "SkeletalMesh importer data editor boundary patch"
ziptest_patch="${scripts}/patches/fileutilities-zip-test-editoronly.patch"
: > "${run}/ziptest-patch-check.log"
apply_patch_idempotent "${ziptest_patch}" "${run}/ziptest-patch-check.log" "FileUtilities zip test editor-only patch"
materialcache_patch="${scripts}/patches/material-cached-expression.patch"
: > "${run}/materialcache-patch-check.log"
apply_patch_idempotent "${materialcache_patch}" "${run}/materialcache-patch-check.log" "Material cached expression fallback patch"
landscape_patch="${scripts}/patches/landscape-editor-boundary.patch"
: > "${run}/landscape-patch-check.log"
apply_patch_idempotent "${landscape_patch}" "${run}/landscape-patch-check.log" "Landscape/PoseWatch editor-only data patch"
packagemetadata_patch="${scripts}/patches/package-metadata-save.patch"
: > "${run}/packagemetadata-patch-check.log"
cp "${packagemetadata_patch}" "${run}/package-metadata-save.patch"
apply_patch_idempotent "${packagemetadata_patch}" "${run}/packagemetadata-patch-check.log" "Package MetaData save-safe lookup patch"
sourcedata_patch="${scripts}/patches/staticmesh-sourcedata-editor-boundary.patch"
: > "${run}/sourcedata-patch-check.log"
cp "${sourcedata_patch}" "${run}/staticmesh-sourcedata-editor-boundary.patch"
apply_patch_idempotent "${sourcedata_patch}" "${run}/sourcedata-patch-check.log" "StaticMeshSourceData editor-only data patch"
scenecapture_patch="${scripts}/patches/scenecapture-editor-decorations.patch"
: > "${run}/scenecapture-patch-check.log"
apply_patch_idempotent "${scenecapture_patch}" "${run}/scenecapture-patch-check.log" "SceneCapture editor decoration mesh patch"
meshdesc_patch="${scripts}/patches/meshdescription-bulkdata-tearoff.patch"
: > "${run}/meshdesc-patch-check.log"
cp "${meshdesc_patch}" "${run}/meshdescription-bulkdata-tearoff.patch"
apply_patch_idempotent "${meshdesc_patch}" "${run}/meshdesc-patch-check.log" "MeshDescription bulk data tear-off patch"
export CARLA_ASSIMP_INSTALL="${artifacts}/assimp-arm64/6.0.5/install"
export CARLA_UFBX_INSTALL="${artifacts}/ufbx-arm64/0.23.0/install"
export CARLA_INTERCHANGE_UFBX_STATIC=0
export CARLA_ARM64_FBX_HEADERS_ONLY=0
export CARLA_DISABLE_ISPC=1
for entry in CarlaAssetProbe.uproject Source/CarlaAssetProbe.Target.cs Source/CarlaAssetProbe; do
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
sha256sum "${BASH_SOURCE[0]}" "${scripts}/check_carla_asset.py" \
  "${scripts}/ufbx-probe/fixtures/multi-mesh.fbx" "${animgraph_patch}" "${reverb_patch}" "${cinematic_patch}" \
  "${instanced_patch}" "${worldpartition_patch}" "${animation_patch}" "${material_patch}" \
  "${staticmesh_patch}" "${skeletalimport_patch}" "${ziptest_patch}" "${materialcache_patch}" "${landscape_patch}" "${seqbase_patch}" \
  "${packagemetadata_patch}" "${sourcedata_patch}" >> "${run}/source-files.sha256"
base=(bash "${ue}/Engine/Build/BatchFiles/Linux/Build.sh" CarlaAssetProbe Linux Development
  -architecture=arm64 "-project=${project_dir}/CarlaAssetProbe.uproject" -NoUBTMakefiles -NoDumpSyms
  -buildubt -ForceRulesCompile "-MaxParallelActions=${jobs}")
step=graph
timeout --kill-after=10 120 "${base[@]}" -Mode=JsonExport "-OutputFile=${run}/target.json" > "${run}/graph.log" 2>&1
step=build
printf "%q " "${base[@]}" > "${run}/build-command.txt"
if ! timeout --kill-after=30 "${build_timeout}" "${base[@]}" "-Log=${run}/ubt.log" > "${run}/build.log" 2>&1; then
  tail -80 "${run}/build.log"; exit 1
fi
binary="${project_dir}/Binaries/Linux/CarlaAssetProbe"
file "${binary}" > "${run}/architecture.log"
grep -q 'ARM aarch64' "${run}/architecture.log"
env -u LD_LIBRARY_PATH -u LD_PRELOAD ldd -r "${binary}" > "${run}/linkage.log" 2>&1
if grep -Eq 'not found|undefined symbol|libfbxsdk' "${run}/linkage.log"; then exit 1; fi
printf '{"graph":0,"build":0,"native":null}\n' > "${run}/processes.json"
layout="${run}/layout"
staged_project="${layout}/CarlaAsset"
staged_bin="${staged_project}/Binaries/Linux"
mkdir -p "${staged_bin}" "${layout}/Engine/Saved" "${run}/assets"
cp --reflink=auto "${binary}" "${staged_bin}/CarlaAssetProbe"
cp "${project_dir}/CarlaAssetProbe.uproject" "${staged_project}/"
cp "${ue}/Engine/Binaries/Linux/CarlaAssetProbe.target" "${staged_bin}/"
for sc_lib in "${ue}/Engine/Binaries/ThirdParty/ShaderConductor/Linux/aarch64-unknown-linux-gnueabi/"libShaderConductor.so "${ue}/Engine/Binaries/ThirdParty/ShaderConductor/Linux/aarch64-unknown-linux-gnueabi/"libdxcompiler.so; do
  [[ -f "${sc_lib}" ]] && cp "${sc_lib}" "${staged_bin}/"
done
for directory in Binaries Build Config Content Platforms Plugins Shaders; do
  if [[ -d "${ue}/Engine/${directory}" ]]; then
    ln -s "${ue}/Engine/${directory}" "${layout}/Engine/${directory}"
  fi
done
find "${layout}" -maxdepth 4 -printf '%y %p -> %l\n' > "${run}/layout.txt"
staged_binary="${staged_bin}/CarlaAssetProbe"
step=native
ulimit -c 0
set +e
timeout --kill-after=10 180 "${staged_binary}" -nullrhi -unattended -nosound -notraceserver \
  -NoSplash -stdout -FullStdOutLogOutput -AllowStdOutLogVerbosity -DDC-ForceMemoryCache \
  "-abslog=${run}/unreal.log" "-project=${staged_project}/CarlaAssetProbe.uproject" \
  "-input=${scripts}/ufbx-probe/fixtures/multi-mesh.fbx" \
  "-content-root=${run}/assets" "-output=${run}/native.json" > "${run}/native.log" 2>&1
process_code=$?
set -e
python3 -c 'import json,sys; print(json.dumps({"graph":0,"build":0,"native":int(sys.argv[1])}))' "${process_code}" > "${run}/processes.json"
sha256sum --check "${run}/source-files.sha256" > "${run}/source-verification.log"
step=validation
python3 "${scripts}/check_carla_asset.py" --program "${staged_binary}" \
  --input "${scripts}/ufbx-probe/fixtures/multi-mesh.fbx" --run-dir "${run}" \
  --ue-root "${ue}" --exit-code "${process_code}" \
  --ufbx-report "${artifacts}/ufbx-arm64/0.23.0/last-verified-stage.json"
