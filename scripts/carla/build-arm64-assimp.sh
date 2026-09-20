#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-8}"
[[ "${jobs}" =~ ^[1-9][0-9]*$ ]] || { echo "CARLA_BUILD_JOBS must be positive" >&2; exit 64; }
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-dev container" >&2
  exit 2
fi
version=6.0.5
commit=392a658f9c271be965271f45e7521a1b80ea4392
export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}"
export CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
source_dir="${artifact_dir}/sources/assimp-v${version}"
build_dir="${artifact_dir}/assimp-arm64/${version}/build"
install_dir="${artifact_dir}/assimp-arm64/${version}/install"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "${artifact_dir}/sources"
run_dir="$(mktemp -d "${artifact_dir}/assimp-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 Assimp FBX Backend\n\n- Status: %s\n- Step: %s\n- Version: %s\n- Commit: %s\n- Scope: independent FBX import/export backend; not an Autodesk SDK ABI replacement or Editor/Cook pass\n" \
    "${status}" "${step}" "${version}" "${commit}" > "${run_dir}/decision.md"
  printf "%s assimp artifacts=%s step=%s\n" "${status}" "${run_dir}" "${step}"
}
trap finish EXIT
run_step() {
  step="$1"
  shift
  printf "step=%s\n" "${step}"
  printf "%q " "$@" > "${run_dir}/${step}.command.txt"
  printf "\n" >> "${run_dir}/${step}.command.txt"
  if "$@" > "${run_dir}/${step}.log" 2>&1; then
    tail -n 10 "${run_dir}/${step}.log"
  else
    local code=$?
    tail -n 80 "${run_dir}/${step}.log"
    return "${code}"
  fi
}
if [[ ! -e "${source_dir}" ]]; then
  run_step clone git clone --depth 1 --branch "v${version}" --filter=blob:none --sparse \
    https://github.com/assimp/assimp.git "${source_dir}"
  run_step sparse-init git -C "${source_dir}" sparse-checkout init --cone
  run_step checkout git -C "${source_dir}" sparse-checkout set \
    cmake-modules code include contrib tools test/unit test/models/FBX
fi
git_cmd=(git -c "safe.directory=${source_dir}" -C "${source_dir}")
[[ "$("${git_cmd[@]}" rev-parse HEAD)" == "${commit}" ]] || {
  echo "Assimp source does not match the pinned commit" >&2
  exit 2
}
"${git_cmd[@]}" diff --exit-code HEAD > "${run_dir}/assimp-source.diff"
"${git_cmd[@]}" rev-parse HEAD > "${run_dir}/assimp-commit.txt"
git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" diff --binary HEAD > "${run_dir}/ue-tracked.patch"
sha256sum "${BASH_SOURCE[0]}" "${script_dir}/ue-arm64-third-party.cmake" \
  "${script_dir}/assimp-probe/CMakeLists.txt" "${script_dir}/assimp-probe/fbx-probe.cpp" \
  "${script_dir}/check-assimp-fbx.py" > "${run_dir}/scripts.sha256"
run_step configure cmake -S "${script_dir}/assimp-probe" -B "${build_dir}" -G Ninja \
  --toolchain "${script_dir}/ue-arm64-third-party.cmake" \
  -DCARLA_ASSIMP_SOURCE_DIR="${source_dir}" -DCMAKE_INSTALL_PREFIX="${install_dir}" \
  -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON \
  -DASSIMP_BUILD_ALL_IMPORTERS_BY_DEFAULT=OFF -DASSIMP_BUILD_FBX_IMPORTER=ON \
  -DASSIMP_BUILD_ALL_EXPORTERS_BY_DEFAULT=OFF -DASSIMP_BUILD_FBX_EXPORTER=ON \
  -DASSIMP_BUILD_ASSBIN_EXPORTER=ON -DASSIMP_BUILD_ASSXML_EXPORTER=ON \
  -DASSIMP_BUILD_TESTS=OFF -DASSIMP_BUILD_ASSIMP_TOOLS=ON -DASSIMP_BUILD_SAMPLES=OFF \
  -DASSIMP_BUILD_ZLIB=ON -DASSIMP_WARNINGS_AS_ERRORS=ON -DASSIMP_INSTALL=ON
run_step build cmake --build "${build_dir}" --parallel "${jobs}" \
  --target assimp_cmd fbx-probe assimp-fbx-tests
step=architecture
for binary in "${build_dir}/lib/libassimp.so" "${build_dir}/bin/fbx-probe" \
  "${build_dir}/bin/assimp" "${build_dir}/bin/assimp-fbx-tests"; do
  file -L "${binary}" | tee -a "${run_dir}/architecture.txt"
  "${CARLA_LLVM_BIN}/llvm-readelf" -h "${binary}" \
    | awk '/Machine:/{found=1; if ($2 != "AArch64") bad=1} END{exit !found || bad}'
done
run_step upstream-fbx env -u LD_LIBRARY_PATH timeout 180 "${build_dir}/bin/assimp-fbx-tests" \
  --gtest_filter=utFBXImporterExporter.* "--gtest_output=xml:${run_dir}/upstream.xml"
run_step install cmake --install "${build_dir}"
install -D -m 0644 "${source_dir}/LICENSE" "${install_dir}/share/licenses/assimp/LICENSE"
run_step deployed-cli env -u LD_LIBRARY_PATH timeout 30 "${install_dir}/bin/assimp" listext
grep -Fq "*.fbx" "${run_dir}/deployed-cli.log"
run_step deployed-linkage env -u LD_LIBRARY_PATH ldd -r "${install_dir}/bin/fbx-probe"
if grep -Eq "not found|undefined symbol" "${run_dir}/deployed-linkage.log"; then
  echo "Assimp probe has unresolved dynamic dependencies" >&2
  exit 1
fi
run_step ue-fbx env -u LD_LIBRARY_PATH python3 "${script_dir}/check-assimp-fbx.py" \
  "${install_dir}/bin/fbx-probe" "${CARLA_UE_DIR}/Engine/Content/FbxEditorAutomation" \
  "${run_dir}/fixtures" --upstream "${run_dir}/upstream.xml"
sha256sum "${install_dir}/bin/fbx-probe" "${install_dir}/bin/assimp" \
  "${install_dir}/lib/libassimp.so" > "${run_dir}/installed.sha256"
run_step stage-report python3 "${script_dir}/stage_report.py" write \
  --output "${run_dir}/stage-report.json" --stage-id assimp-fbx-backend \
  --scope "Assimp FBX backend only; not Autodesk SDK ABI or Unreal Editor/Cook" --exit-code 0 \
  --required-check upstream --check upstream PASS --evidence upstream "${run_dir}/upstream.xml" \
  --required-check fixtures --check fixtures PASS --evidence fixtures "${run_dir}/fixtures/summary.json" \
  --required-check architecture --check architecture PASS --evidence architecture "${run_dir}/architecture.txt" \
  --required-check linkage --check linkage PASS --evidence linkage "${run_dir}/deployed-linkage.log" \
  --evidence library "${install_dir}/lib/libassimp.so" --evidence probe "${install_dir}/bin/fbx-probe" \
  --evidence scripts "${run_dir}/scripts.sha256" --evidence ue-patch "${run_dir}/ue-tracked.patch" \
  --source assimp "${source_dir}" "${commit}" \
  --source ue "${CARLA_UE_DIR}" "$(cat "${run_dir}/ue-commit.txt")" \
  --command bash "${BASH_SOURCE[0]}"
ln -sfn "${run_dir}/stage-report.json" "${artifact_dir}/assimp-arm64/${version}/last-verified-stage.json"
step=complete
