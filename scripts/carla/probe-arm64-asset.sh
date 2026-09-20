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
if "${engine_git[@]}" apply --reverse --check "${animation_patch}" > "${run}/animation-patch-check.log" 2>&1; then
  printf "Animation editor-only data patch is already applied\n" >> "${run}/animation-patch-check.log"
else
  "${engine_git[@]}" apply --check "${animation_patch}" >> "${run}/animation-patch-check.log" 2>&1
  "${engine_git[@]}" apply "${animation_patch}"
fi
cp "${animation_patch}" "${run}/"
material_patch="${scripts}/patches/material-editor-boundary.patch"
if "${engine_git[@]}" apply --reverse --check "${material_patch}" > "${run}/material-patch-check.log" 2>&1; then
  printf "Material editor API boundary patch is already applied\n" >> "${run}/material-patch-check.log"
else
  "${engine_git[@]}" apply --check "${material_patch}" >> "${run}/material-patch-check.log" 2>&1
  "${engine_git[@]}" apply "${material_patch}"
fi
cp "${material_patch}" "${run}/"
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
  "${instanced_patch}" "${worldpartition_patch}" "${animation_patch}" "${material_patch}" >> "${run}/source-files.sha256"
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
  -NoSplash -stdout -FullStdOutLogOutput -AllowStdOutLogVerbosity \
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
