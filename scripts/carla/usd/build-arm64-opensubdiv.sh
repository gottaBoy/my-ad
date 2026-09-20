#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-4}"
timeout_seconds="${CARLA_OPENSUBDIV_TIMEOUT_SECONDS:-600}"
[[ "${jobs}" =~ ^[1-4]$ ]] || { echo "CARLA_BUILD_JOBS must be an integer from 1 to 4" >&2; exit 64; }
[[ "${timeout_seconds}" =~ ^[1-9][0-9]{0,3}$ && "${timeout_seconds}" -le 1200 ]] || {
  echo "CARLA_OPENSUBDIV_TIMEOUT_SECONDS must be an integer from 1 to 1200" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run in native ARM64 carla-dev Docker" >&2; exit 2;
}
export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}"
export CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CPATH CPLUS_INCLUDE_PATH C_INCLUDE_PATH LIBRARY_PATH
unset CC CXX MAKEFLAGS CMAKE_PREFIX_PATH LD_LIBRARY_PATH LD_PRELOAD
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
module="${CARLA_UE_DIR}/Engine/Source/ThirdParty/OpenSubdiv"
source_dir="${module}/OpenSubdiv-3.6.0"
usd_build="${CARLA_UE_DIR}/Engine/Plugins/Runtime/USDCore/Source/ThirdParty/USD/BuildForLinux.sh"
triple=aarch64-unknown-linux-gnueabi
libcxx="${CARLA_UE_DIR}/Engine/Source/ThirdParty/Unix/LibCxx"
libcxx_lib="${libcxx}/lib/Unix/${triple}"
artifact_root="${CARLA_ARTIFACT_DIR:-/artifacts/carla}/usd"

check_output() {
  python3 - "$1" "${CARLA_UE_DIR}" "${CARLA_SOURCE_DIR:-/workspace/carla}" <<'PY'
from pathlib import Path
import sys
output, *sources = [Path(p).resolve() for p in sys.argv[1:]]
if any(output.is_relative_to(source) for source in sources):
    raise ValueError("refusing to write OpenSubdiv artifacts into a source tree")
PY
}

run_step() {
  local step="$1" code
  shift
  printf '%q ' "$@" > "${run_dir}/${step}.command.txt"
  printf '\n' >> "${run_dir}/${step}.command.txt"
  printf 'step=%s\n' "${step}"
  # Do not put functions in an if-condition: that would disable their errexit.
  set +e
  ( set -e; "$@" ) > "${run_dir}/${step}.log" 2>&1
  code=$?
  set -e
  printf '%s\n' "${code}" > "${run_dir}/${step}.exit-code.txt"
  tail -n 10 "${run_dir}/${step}.log"
  [[ "${code}" == 0 ]] || return "${code}"
  touch "${run_dir}/passed/${step}"
}

preflight() {
  printf 'container=docker\nhost=%s\ntarget=%s\n' "$(uname -m)" "${triple}"
  local path
  for path in CMakeLists.txt opensubdiv-config.cmake.in opensubdiv/version.h \
    opensubdiv/CMakeLists.txt opensubdiv/far/topologyDescriptor.cpp \
    opensubdiv/far/topologyRefiner.cpp opensubdiv/far/stencilTableFactory.cpp \
    opensubdiv/osd/cpuEvaluator.cpp; do
    [[ -s "${source_dir}/${path}" ]] || { echo "missing OpenSubdiv source: ${source_dir}/${path}" >&2; return 2; }
  done
  grep -Eq '^#define OPENSUBDIV_VERSION_NUMBER 30600$' "${source_dir}/opensubdiv/version.h"
  for path in "${module}/OpenSubdiv.Build.cs" "${module}/BuildForUE/Linux/BuildForLinux.sh" \
    "${usd_build}" "${libcxx}/include/c++/v1/__config" \
    "${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a"; do
    [[ -s "${path}" ]] || { echo "missing UE input: ${path}" >&2; return 2; }
  done
  grep -Fq OpenSubdiv-3.6.0 "${module}/OpenSubdiv.Build.cs"
  grep -Fq OpenSubdiv-3.6.0 "${usd_build}"
  grep -Fq libosdCPU "${module}/OpenSubdiv.Build.cs"
  for path in clang clang++ llvm-ar llvm-ranlib llvm-readelf ld.lld; do
    [[ -x "${CARLA_LLVM_BIN}/${path}" ]] || { echo "missing native LLVM tool: ${path}" >&2; return 2; }
  done
  cmake --version
  ninja --version
  shopt -s nullglob
  sysroots=("${CARLA_UE_DIR}"/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v*_clang-*/"${triple}")
  [[ "${#sysroots[@]}" == 1 ]] || { echo "expected exactly one UE ARM64 sysroot" >&2; return 2; }
  printf '%s\n' "${sysroots[0]}" > "${run_dir}/sysroot.txt"
  "${CARLA_LLVM_BIN}/clang++" --version
  "${CARLA_LLVM_BIN}/clang++" --target="${triple}" --sysroot="${sysroots[0]}" -dumpmachine
  git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
}

retain_source() {
  cp "${BASH_SOURCE[0]}" "${script_dir}/opensubdiv-smoke.cpp" "${run_dir}/inputs/"
  cp "${script_dir}/../ue-arm64-third-party.cmake" "${script_dir}/../stage_report.py" "${run_dir}/inputs/"
  cp "${module}/OpenSubdiv.Build.cs" "${run_dir}/inputs/"
  cp "${module}/BuildForUE/Linux/BuildForLinux.sh" "${run_dir}/inputs/OpenSubdiv-BuildForLinux.sh"
  cp "${usd_build}" "${run_dir}/inputs/USD-BuildForLinux.sh"
  python3 - "${source_dir}" "${run_dir}" <<'PY'
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
source, run = map(Path, sys.argv[1:])
records = {}
for path in sorted(source.rglob("*")):
    mode = path.lstat().st_mode
    if stat.S_ISDIR(mode):
        continue
    if not stat.S_ISREG(mode):
        raise ValueError(f"nonregular source input: {path}")
    records[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
if not records:
    raise ValueError("empty OpenSubdiv source")
shutil.copytree(source, run / "source")
(run / "source.sha256.json").write_text(json.dumps(records, sort_keys=True, indent=2) + "\n")
print(f"Retained {len(records)} local UE source files; not a claimed pristine upstream tag")
PY
  git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" diff --binary HEAD -- \
    Engine/Source/ThirdParty/OpenSubdiv > "${run_dir}/ue-opensubdiv.patch"
  sha256sum "${CARLA_LLVM_BIN}/clang" "${CARLA_LLVM_BIN}/clang++" \
    "${CARLA_LLVM_BIN}/llvm-ar" "${CARLA_LLVM_BIN}/llvm-ranlib" "${CARLA_LLVM_BIN}/ld.lld" \
    "${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a" > "${run_dir}/toolchain.sha256"
  sha256sum "${BASH_SOURCE[0]}" "${script_dir}/opensubdiv-smoke.cpp" \
    "${script_dir}/../ue-arm64-third-party.cmake" "${script_dir}/../stage_report.py" \
    "${module}/OpenSubdiv.Build.cs" "${module}/BuildForUE/Linux/BuildForLinux.sh" \
    "${usd_build}" > "${run_dir}/inputs.sha256"
}

check_archive() {
  "${CARLA_LLVM_BIN}/llvm-ar" t "${archive}" > "${run_dir}/archive-members.txt"
  "${CARLA_LLVM_BIN}/llvm-readelf" -h "${archive}" > "${run_dir}/archive-readelf.txt"
  python3 - "${run_dir}/archive-members.txt" "${run_dir}/archive-readelf.txt" <<'PY'
from pathlib import Path
import re
import sys
members = Path(sys.argv[1]).read_text().splitlines()
text = Path(sys.argv[2]).read_text()
if not members:
    raise ValueError("empty osdCPU archive")
for field, wanted in (("Class", "ELF64"), ("Machine", "AArch64"), ("Type", "REL")):
    values = re.findall(r"^\s*" + field + r":\s+(\S+)", text, re.M)
    if len(values) != len(members) or any(value != wanted for value in values):
        raise ValueError(f"archive {field} mismatch: {values}")
print(f"PASS: all {len(members)} osdCPU archive members are AArch64 ELF64 REL")
PY
}

check_architecture() {
  local binary
  for binary in "${shared}" "${smoke}"; do
    "${CARLA_LLVM_BIN}/llvm-readelf" -h -d "${binary}" > "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Class:[[:space:]]+ELF64' "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Machine:[[:space:]]+AArch64' "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Type:[[:space:]]+DYN' "${run_dir}/$(basename "${binary}").readelf.txt"
    if grep -Eq 'TEXTREL|libstdc\+\+' "${run_dir}/$(basename "${binary}").readelf.txt"; then
      echo "TEXTREL or unexpected libstdc++ dependency: ${binary}" >&2; return 1
    fi
    file "${binary}"
  done
}

check_linkage() {
  ldd -r "${shared}" > "${run_dir}/shared-linkage.txt" 2>&1
  ldd -r "${smoke}" > "${run_dir}/smoke-linkage.txt" 2>&1
  if grep -E 'not found|undefined symbol|libstdc\+\+' "${run_dir}/shared-linkage.txt" "${run_dir}/smoke-linkage.txt"; then
    return 1
  fi
  cat "${run_dir}/shared-linkage.txt" "${run_dir}/smoke-linkage.txt"
}

check_retention() {
  sha256sum --check "${run_dir}/toolchain.sha256"
  sha256sum --check "${run_dir}/inputs.sha256"
  python3 - "${source_dir}" "${run_dir}" <<'PY'
import hashlib
import json
from pathlib import Path
import stat
import sys
source, run = map(Path, sys.argv[1:])
expected = json.loads((run / "source.sha256.json").read_text())
for root in (source, run / "source"):
    actual = {}
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"nonregular source: {path}")
        actual[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"source changed during build: {root}")
print("PASS: original and retained OpenSubdiv source sets and SHA256 are unchanged")
PY
  (cd "${prefix}" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "${run_dir}/installed.sha256"
}

worker() {
  run_dir="$1"
  check_output "${run_dir}"
  [[ -d "${run_dir}/passed" && -d "${run_dir}/inputs" && ! -e "${run_dir}/source" \
    && ! -e "${run_dir}/work" && ! -e "${run_dir}/install" ]] || {
    echo "worker requires a fresh supervisor directory" >&2; return 2;
  }
  prefix="${run_dir}/install"
  archive="${prefix}/lib/libosdCPU.a"
  shared="${prefix}/lib/libopensubdiv-smoke.so"
  smoke="${prefix}/bin/opensubdiv-smoke"
  run_step preflight preflight
  run_step source retain_source
  run_step configure cmake -S "${run_dir}/source" -B "${run_dir}/work" -G Ninja \
    --toolchain "${run_dir}/inputs/ue-arm64-third-party.cmake" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="${prefix}" \
    -DCMAKE_INSTALL_INCLUDEDIR=include -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_INCDIR_BASE=include/opensubdiv -DCMAKE_LIBDIR_BASE=lib -DCMAKE_BINDIR_BASE=bin \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DBUILD_SHARED_LIBS=OFF -DNO_LIB=OFF \
    -DNO_REGRESSION=ON -DNO_TESTS=ON -DNO_DOC=ON -DNO_EXAMPLES=ON -DNO_TUTORIALS=ON \
    -DNO_PTEX=ON -DNO_TBB=ON -DNO_OMP=ON -DNO_CUDA=ON -DNO_OPENCL=ON \
    -DNO_OPENGL=ON -DNO_DX=ON -DNO_GLEW=ON -DNO_GLFW=ON -DNO_METAL=ON -DNO_CLEW=ON
  run_step build cmake --build "${run_dir}/work" --parallel "${jobs}" --verbose
  run_step install cmake --install "${run_dir}/work"
  run_step archive check_archive
  local sysroot
  sysroot="$(< "${run_dir}/sysroot.txt")"
  common=("${CARLA_LLVM_BIN}/clang++" "--target=${triple}" "--sysroot=${sysroot}"
    -std=c++14 -fPIC -fuse-ld=lld -stdlib=libc++ -nostdinc++
    -isystem "${libcxx}/include" -isystem "${libcxx}/include/c++/v1"
    -I"${prefix}/include" "-L${libcxx_lib}")
  runtime=("${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a" -lm -lpthread -ldl)
  run_step smoke-library "${common[@]}" -shared -Wl,-z,defs -Wl,-z,text \
    -Wl,--exclude-libs,ALL -Wl,-soname,libopensubdiv-smoke.so \
    "${run_dir}/inputs/opensubdiv-smoke.cpp" \
    -Wl,--whole-archive "${archive}" -Wl,--no-whole-archive "${runtime[@]}" -o "${shared}"
  mkdir -p "${prefix}/bin"
  run_step smoke-compile "${common[@]}" -fPIE -pie -DOPENSUBDIV_SMOKE_DRIVER \
    "${run_dir}/inputs/opensubdiv-smoke.cpp" "-L${prefix}/lib" -lopensubdiv-smoke \
    '-Wl,-rpath,$ORIGIN/../lib' "${runtime[@]}" -o "${smoke}"
  run_step architecture check_architecture
  run_step linkage check_linkage
  run_step smoke "${smoke}"
  grep -Fxq 'OpenSubdiv 3.6.0 osdCPU smoke PASS Catmull-Clark/stencils/libc++' "${run_dir}/smoke.log"
  run_step retention check_retention
}

if [[ "${1:-}" == --worker && "$#" == 2 ]]; then
  worker "$2"
  exit 0
fi
[[ "$#" == 0 ]] || { echo "No arguments expected; configure CARLA_* variables" >&2; exit 64; }
check_output "${artifact_root}"
mkdir -p "${artifact_root}"
run_dir="$(mktemp -d "${artifact_root}/opensubdiv-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
mkdir "${run_dir}/inputs" "${run_dir}/passed"
printf 'opensubdiv artifacts=%s\n' "${run_dir}"
command=(timeout --kill-after=15s "${timeout_seconds}s" bash "${BASH_SOURCE[0]}" --worker "${run_dir}")
printf '%q ' "${command[@]}" > "${run_dir}/worker.command.txt"
printf '\n' >> "${run_dir}/worker.command.txt"
code=0
"${command[@]}" > "${run_dir}/worker.log" 2>&1 || code=$?
printf '%s\n' "${code}" > "${run_dir}/exit-code.txt"
tail -n 30 "${run_dir}/worker.log"

# The supervisor outlives a timed-out worker and never promotes partial output.
python3 - "${run_dir}" "${script_dir}/../stage_report.py" "${code}" "${CARLA_UE_DIR}" "${BASH_SOURCE[0]}" <<'PY'
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
run, reporter, code, ue, recipe = sys.argv[1:]
run, code = Path(run), int(code)
spec = importlib.util.spec_from_file_location("stage_report", reporter)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
stage_id = "carla-opensubdiv-native-arm64"
scope = ("Native ARM64 OpenSubdiv 3.6.0 Release osdCPU static PIC and subdivision smoke ONLY; "
         "not GPU backends, Python, full OpenSubdiv, OpenUSD or UE Editor/Cook")
names = ("preflight", "source", "configure", "build", "install", "archive", "smoke-library",
         "smoke-compile", "architecture", "linkage", "smoke", "retention", "worker-exit")
checks = {name: "PASS" if (run / "passed" / name).is_file() else "FAIL" for name in names}
checks["worker-exit"] = "PASS" if code == 0 else "FAIL"
evidence = {name: run / (name + ".log") if (run / (name + ".log")).is_file()
            else run / "worker.log" for name in names}
evidence["worker-exit"] = run / "exit-code.txt"
for path in sorted(run.rglob("*")):
    relative = path.relative_to(run)
    if path.is_file() and relative.parts[0] not in ("passed", "work"):
        evidence["retained." + relative.as_posix()] = path
for name in ("CMakeCache.txt", "compile_commands.json", "build.ninja"):
    if (run / "work" / name).is_file():
        evidence["cmake." + name] = run / "work" / name
identity = run / "source.sha256.json"
revision = hashlib.sha256(identity.read_bytes()).hexdigest() if identity.is_file() else "unverified"
commit = run / "ue-commit.txt"
report = stage.write_report(
    run / "stage-report.json", stage_id=stage_id, scope=scope, exit_code=code,
    required_checks=list(names), checks=checks, evidence=evidence,
    sources={"opensubdiv": {"location": str(run / "source"), "revision": revision},
             "ue": {"location": ue, "revision": commit.read_text().strip() if commit.is_file() else "unverified"}},
    command=["bash", recipe],
)
(run / "decision.json").write_text(json.dumps(
    {"status": report["status"], "exit_code": code, "scope": scope,
     "failed_checks": [name for name in names if checks[name] != "PASS"]}, indent=2) + "\n")
if report["status"] == "PASS":
    stage.validate_report(run / "stage-report.json", stage_id=stage_id, scope=scope)
    (run / "report-validation.log").write_text(f"PASS {stage_id}\n")
print(f"{report['status']} {stage_id} report={run / 'stage-report.json'} exit={code}")
sys.exit(0 if report["status"] == "PASS" else 1)
PY
