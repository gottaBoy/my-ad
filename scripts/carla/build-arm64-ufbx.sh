#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-4}"
[[ "${jobs}" =~ ^[1-4]$ ]] || { echo "CARLA_BUILD_JOBS must be an integer from 1 to 4" >&2; exit 64; }
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this substage in the native ARM64 carla-dev container" >&2
  exit 2
fi
version=0.23.0
tag=v0.23.0
commit=fcc5d6ba444cfd3eb80677dba5e37e493941abe5
repository=https://github.com/ufbx/ufbx.git
scope="ufbx static FBX backend only; not Autodesk SDK ABI, UE Editor/Cook or RPC"
export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}"
export CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
source_dir="${artifact_dir}/sources/ufbx-v${version}"
build_dir="${artifact_dir}/ufbx-arm64/${version}/build"
install_dir="${artifact_dir}/ufbx-arm64/${version}/install"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "${artifact_dir}/sources"
run_dir="$(mktemp -d "${artifact_dir}/ufbx-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
actual_commit=unverified

finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 && "${step}" == complete ]] && status=PASS
  if [[ ! -s "${run_dir}/stage-report.json" ]]; then
    python3 "${script_dir}/stage_report.py" write --output "${run_dir}/stage-report.json" \
      --stage-id ufbx-fbx-static-backend --scope "${scope}" --exit-code "${code}" \
      --required-check build --check build FAIL --evidence build "${run_dir}/build.log" \
      --source ufbx "${source_dir}" "${actual_commit}" --command bash "${BASH_SOURCE[0]}" \
      > "${run_dir}/report-error.log" 2>&1 || true
  fi
  printf "# Native ARM64 ufbx Static Backend\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Tag: %s\n- Commit: %s\n- Scope: %s\n" \
    "${status}" "${step}" "${code}" "${tag}" "${actual_commit}" "${scope}" > "${run_dir}/decision.md"
  printf "%s ufbx artifacts=%s step=%s exit=%s\n" "${status}" "${run_dir}" "${step}" "${code}"
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
    tail -n 60 "${run_dir}/${step}.log"
    return "${code}"
  fi
}

if [[ ! -e "${source_dir}" ]]; then
  run_step clone git clone --depth 1 --branch "${tag}" --filter=blob:none --sparse "${repository}" "${source_dir}"
fi
git_cmd=(git -c "safe.directory=${source_dir}" -C "${source_dir}")
actual_commit="$("${git_cmd[@]}" rev-parse HEAD)"
[[ "${actual_commit}" == "${commit}" && "$("${git_cmd[@]}" rev-parse "${tag}^{commit}")" == "${commit}" ]] || {
  echo "ufbx checkout/tag does not match the pinned commit" >&2; exit 2;
}
[[ "$("${git_cmd[@]}" remote get-url origin)" == "${repository}" ]] || {
  echo "ufbx origin is not the pinned official repository" >&2; exit 2;
}
run_step source-clean "${git_cmd[@]}" diff --exit-code HEAD
[[ -z "$("${git_cmd[@]}" status --porcelain)" ]] || { echo "ufbx source tree is dirty" >&2; exit 2; }
run_step sparse-init "${git_cmd[@]}" sparse-checkout init --cone
run_step sparse-source "${git_cmd[@]}" sparse-checkout set test
printf "%s\n" "${actual_commit}" > "${run_dir}/ufbx-commit.txt"
"${git_cmd[@]}" show --no-patch --format=fuller "${tag}" > "${run_dir}/tag.txt"
python3 -c 'import json,sys; print(json.dumps(dict(zip(("repository","tag","commit"),sys.argv[1:])),indent=2))' \
  "${repository}" "${tag}" "${commit}" > "${run_dir}/source-identity.json"
ue_git=(git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}")
"${ue_git[@]}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
"${ue_git[@]}" diff --binary HEAD > "${run_dir}/ue-tracked.patch"
find "${script_dir}/ufbx-probe" -type f -print0 | sort -z | xargs -0 sha256sum > "${run_dir}/source-files.sha256"
sha256sum "${source_dir}/ufbx.c" "${source_dir}/ufbx.h" "${source_dir}/test/unit_tests.c" \
  "${source_dir}/LICENSE" "${source_dir}/README.md" >> "${run_dir}/source-files.sha256"
sha256sum "${BASH_SOURCE[0]}" "${script_dir}/check-ufbx-fbx.py" "${script_dir}/stage_report.py" \
  "${script_dir}/ue-arm64-third-party.cmake" > "${run_dir}/scripts.sha256"

run_step configure cmake -S "${script_dir}/ufbx-probe" -B "${build_dir}" -G Ninja \
  --toolchain "${script_dir}/ue-arm64-third-party.cmake" -DCARLA_UFBX_SOURCE_DIR="${source_dir}" \
  -DCMAKE_INSTALL_PREFIX="${install_dir}" -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
run_step build cmake --build "${build_dir}" --parallel "${jobs}"
run_step install cmake --install "${build_dir}"
step=architecture
for binary in "${install_dir}/bin/ufbx-probe" "${install_dir}/bin/ufbx-unit-tests" "${install_dir}/lib/libufbx.so"; do
  file -L "${binary}" | tee -a "${run_dir}/architecture.txt"
  "${CARLA_LLVM_BIN}/llvm-readelf" -h "${binary}" \
    | awk '/Machine:/{found=1; if ($2 != "AArch64") bad=1} END{exit !found || bad}'
done
run_step static-members "${CARLA_LLVM_BIN}/llvm-ar" t "${install_dir}/lib/libufbx.a"
run_step static-architecture "${CARLA_LLVM_BIN}/llvm-readelf" -h "${install_dir}/lib/libufbx.a"
awk '/Machine:/{objects++; if ($2 != "AArch64") bad=1}
     /Class:/{if ($2 != "ELF64") bad=1}
     /Type:/{if ($2 != "REL") bad=1}
     END{exit !objects || bad}' "${run_dir}/static-architecture.log"
run_step linkage env -u LD_LIBRARY_PATH ldd -r "${install_dir}/bin/ufbx-probe"
if grep -Eq "not found|undefined symbol" "${run_dir}/linkage.log"; then
  echo "ufbx probe has unresolved runtime dependencies" >&2; exit 1
fi
run_step unchanged-sources sha256sum --check "${run_dir}/source-files.sha256"
cp "${build_dir}/compile_commands.json" "${run_dir}/compile-command.json"
sha256sum "${install_dir}/bin/ufbx-probe" "${install_dir}/bin/ufbx-unit-tests" \
  "${install_dir}/lib/libufbx.so" "${install_dir}/lib/libufbx.a" > "${run_dir}/installed.sha256"
ulimit -c 0
run_step validation env -u LD_LIBRARY_PATH python3 "${script_dir}/check-ufbx-fbx.py" \
  --program "${install_dir}/bin/ufbx-probe" --upstream "${install_dir}/bin/ufbx-unit-tests" \
  --fixtures "${CARLA_UE_DIR}/Engine/Content/FbxEditorAutomation" \
  --control-fixture "${script_dir}/ufbx-probe/fixtures/hierarchy-geometry.fbx" \
  --run-dir "${run_dir}" --source-dir "${source_dir}" --ue-root "${CARLA_UE_DIR}" \
  --library "${install_dir}/lib/libufbx.so" --static-library "${install_dir}/lib/libufbx.a"
run_step report-validation python3 "${script_dir}/stage_report.py" validate "${run_dir}/stage-report.json" \
  --stage-id ufbx-fbx-static-backend --scope "${scope}"
ln -sfn "${run_dir}/stage-report.json" "${artifact_dir}/ufbx-arm64/${version}/last-verified-stage.json"
step=complete
