#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-4}"
timeout_seconds="${CARLA_ALEMBIC_TIMEOUT_SECONDS:-1200}"
[[ "${jobs}" =~ ^[1-4]$ ]] || { echo "CARLA_BUILD_JOBS must be an integer from 1 to 4" >&2; exit 64; }
[[ "${timeout_seconds}" =~ ^[1-9][0-9]{0,3}$ && "${timeout_seconds}" -le 1200 ]] || {
  echo "CARLA_ALEMBIC_TIMEOUT_SECONDS must be an integer from 1 to 1200" >&2
  exit 64
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run this script in the native ARM64 carla-dev container" >&2
  exit 2
}

export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}"
export CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CPATH CPLUS_INCLUDE_PATH C_INCLUDE_PATH LIBRARY_PATH
unset CC CXX MAKEFLAGS CMAKE_PREFIX_PATH LD_LIBRARY_PATH LD_PRELOAD ALEMBIC_INSTALL_PREFIX
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
module="${CARLA_UE_DIR}/Engine/Source/ThirdParty/Alembic"
source_dir="${CARLA_ALEMBIC_SOURCE_DIR:-${module}/alembic-1.8.6}"
imath_report="${CARLA_IMATH_REPORT:-}"
[[ -n "${imath_report}" ]] || { echo "CARLA_IMATH_REPORT is required (verified native report path)" >&2; exit 64; }
triple=aarch64-unknown-linux-gnueabi
libcxx="${CARLA_UE_DIR}/Engine/Source/ThirdParty/Unix/LibCxx"
libcxx_lib="${libcxx}/lib/Unix/${triple}"

run_step() {
  local step="$1"
  shift
  printf '%q ' "$@" > "${run_dir}/${step}.command.txt"
  printf '\n' >> "${run_dir}/${step}.command.txt"
  printf 'step=%s\n' "${step}"
  # Do not call this function in a conditional: functions must retain errexit.
  ( "$@" ) > "${run_dir}/${step}.log" 2>&1
  touch "${run_dir}/passed/${step}"
  tail -n 8 "${run_dir}/${step}.log"
}

verify_imath() {
  python3 - "${imath_report}" "${run_dir}" "${script_dir}/../stage_report.py" "${CARLA_UE_DIR}" "$1" <<'PY'
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys

filename, output, reporter, ue, phase = sys.argv[1:]
path, run = Path(filename).absolute(), Path(output)
spec = importlib.util.spec_from_file_location("stage_report", reporter)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
scope = "Native ARM64 Imath 3.1.9 Release static PIC library and smoke; not PyImath, OpenUSD or UE Editor"

def digest(file):
    if not stat.S_ISREG(file.lstat().st_mode):
        raise ValueError(f"nonregular Imath input: {file}")
    return hashlib.sha256(file.read_bytes()).hexdigest()

checksum = digest(path)
report = stage.validate_report(path, stage_id="carla-imath-native-arm64", scope=scope)
base = path.parent
evidence = report["evidence"]

def retained(relative):
    file = base / relative
    item = evidence.get("retained." + relative)
    if not isinstance(item, dict) or item.get("path") != relative or item.get("sha256") != digest(file):
        raise ValueError(f"Imath report does not bind required input: {relative}")
    return file

source_manifest = retained("source.sha256.json")
expected = json.loads(source_manifest.read_text())
if not isinstance(expected, dict) or not expected:
    raise ValueError("empty Imath source manifest")
for root in (base / "source", Path(ue) / "Engine/Source/ThirdParty/Imath/Imath-3.1.9"):
    actual = {}
    for file in sorted(root.rglob("*")):
        if stat.S_ISDIR(file.lstat().st_mode):
            continue
        name = file.relative_to(root).as_posix()
        actual[name] = digest(file)
        retained("source/" + name)
    if actual != expected:
        raise ValueError(f"Imath source set/hash mismatch: {root}")
for name in ("lib/libImath-3_1.a", "include/Imath/ImathConfig.h",
             "lib/cmake/Imath/ImathConfig.cmake", "lib/cmake/Imath/ImathConfigVersion.cmake",
             "lib/cmake/Imath/ImathTargets.cmake", "lib/cmake/Imath/ImathTargets-release.cmake"):
    retained("install/" + name)
prefix = base / "install"
for file in sorted(prefix.rglob("*")):
    if not stat.S_ISDIR(file.lstat().st_mode):
        retained(file.relative_to(base).as_posix())
if report["sources"]["imath"]["revision"] != digest(source_manifest):
    raise ValueError("Imath source revision mismatch")
if digest(path) != checksum:
    raise ValueError("Imath report changed during verification")
pin = {"report": str(path), "sha256": checksum, "prefix": str(prefix),
       "source_sha256": digest(source_manifest), "source_files": len(expected),
       "library_sha256": digest(prefix / "lib/libImath-3_1.a")}
if phase == "before":
    with (run / "imath-pin.json").open("x") as stream:
        json.dump(pin, stream, indent=2)
    with (run / "inputs/imath-stage-report.json").open("xb") as stream:
        stream.write(path.read_bytes())
    (run / "imath-prefix.txt").write_text(str(prefix) + "\n")
elif json.loads((run / "imath-pin.json").read_text()) != pin:
    raise ValueError("Imath prerequisite changed during Alembic build")
print(json.dumps({"status": "PASS", "phase": phase, **pin}, indent=2))
PY
}

preflight() {
  printf 'container=docker\nhost=%s\ntarget=%s\n' "$(uname -m)" "${triple}"
  local path
  for path in CMakeLists.txt cmake/AlembicIlmBase.cmake lib/CMakeLists.txt \
    lib/Alembic/CMakeLists.txt lib/Alembic/AlembicConfig.cmake.in \
    lib/Alembic/Util/Config.h.in lib/Alembic/AbcCoreOgawa/ReadWrite.cpp \
    lib/Alembic/Ogawa/CMakeLists.txt; do
    [[ -s "${source_dir}/${path}" ]] || { echo "missing Alembic source: ${source_dir}/${path}" >&2; return 2; }
  done
  grep -Eq '^PROJECT\(Alembic VERSION 1\.8\.6\)' "${source_dir}/CMakeLists.txt"
  for path in clang clang++ llvm-ar llvm-ranlib llvm-readelf ld.lld; do
    [[ -x "${CARLA_LLVM_BIN}/${path}" ]] || { echo "missing native LLVM tool: ${path}" >&2; return 2; }
  done
  for path in "${libcxx}/include/c++/v1/__config" "${libcxx_lib}/libc++.a" \
    "${libcxx_lib}/libc++abi.a" "${module}/AlembicLib.Build.cs" "${module}/BuildForLinux.sh"; do
    [[ -s "${path}" ]] || { echo "missing UE input: ${path}" >&2; return 2; }
  done
  command -v cmake
  command -v ninja
  shopt -s nullglob
  sysroots=("${CARLA_UE_DIR}"/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v*_clang-*/"${triple}")
  [[ "${#sysroots[@]}" == 1 ]] || { echo "expected exactly one UE ARM64 sysroot" >&2; return 2; }
  printf '%s\n' "${sysroots[0]}" > "${run_dir}/sysroot.txt"
  "${CARLA_LLVM_BIN}/clang++" --version
  "${CARLA_LLVM_BIN}/clang++" --target="${triple}" --sysroot="${sysroots[0]}" -dumpmachine
  git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
  sha256sum --check "$(dirname "${imath_report}")/toolchain.sha256"
}

retain_source() {
  cp "${BASH_SOURCE[0]}" "${run_dir}/inputs/build-arm64-alembic.sh"
  cp "${script_dir}/alembic-smoke.cpp" "${run_dir}/inputs/"
  cp "${script_dir}/../ue-arm64-third-party.cmake" "${script_dir}/../stage_report.py" "${run_dir}/inputs/"
  cp "${module}/AlembicLib.Build.cs" "${module}/BuildForLinux.sh" "${run_dir}/inputs/"
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
    if stat.S_ISDIR(path.lstat().st_mode):
        continue
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"nonregular source input: {path}")
    records[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
if not records:
    raise ValueError("empty Alembic source")
shutil.copytree(source, run / "source")
(run / "source.sha256.json").write_text(json.dumps(records, sort_keys=True, indent=2) + "\n")
print(f"Retained {len(records)} local Alembic source files; no source synthesis")
PY
  git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" diff --binary HEAD -- \
    Engine/Source/ThirdParty/Alembic > "${run_dir}/ue-alembic.patch"
  sha256sum "${CARLA_LLVM_BIN}/clang" "${CARLA_LLVM_BIN}/clang++" \
    "${CARLA_LLVM_BIN}/llvm-ar" "${CARLA_LLVM_BIN}/llvm-ranlib" "${CARLA_LLVM_BIN}/ld.lld" \
    "${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a" > "${run_dir}/toolchain.sha256"
  sha256sum "${script_dir}/alembic-smoke.cpp" "${BASH_SOURCE[0]}" \
    "${script_dir}/../ue-arm64-third-party.cmake" "${script_dir}/../stage_report.py" \
    "${module}/AlembicLib.Build.cs" "${module}/BuildForLinux.sh" > "${run_dir}/inputs.sha256"
}

check_configuration() {
  python3 - "${run_dir}/work/CMakeCache.txt" "${imath_prefix}" <<'PY'
from pathlib import Path
import sys
cache = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if line and not line.startswith(("#", "//")) and "=" in line:
        key, value = line.split("=", 1)
        cache[key.split(":", 1)[0]] = value
for name, value in {"USE_HDF5": "OFF", "USE_PYALEMBIC": "OFF", "USE_TESTS": "OFF",
                    "USE_BINARIES": "OFF", "ALEMBIC_SHARED_LIBS": "OFF",
                    "CMAKE_POSITION_INDEPENDENT_CODE": "ON",
                    "CMAKE_BUILD_TYPE": "Release"}.items():
    if cache.get(name) != value:
        raise ValueError(f"unexpected Alembic configuration: {name}")
if Path(cache["Imath_DIR"]).resolve() != (Path(sys.argv[2]) / "lib/cmake/Imath").resolve():
    raise ValueError("CMake did not select the verified Imath prefix")
print("PASS: selected verified Imath; Release/PIC/static, HDF5/Python OFF")
PY
  grep -Fq 'Found package Imath' "${run_dir}/configure.log"
  if grep -Fq 'looking for IlmBase instead' "${run_dir}/configure.log"; then
    echo "unexpected IlmBase fallback" >&2
    return 1
  fi
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
    raise ValueError("empty Alembic archive")
for field, wanted in (("Class", "ELF64"), ("Machine", "AArch64"), ("Type", "REL")):
    values = re.findall(r"^\s*" + field + r":\s+(\S+)", text, re.M)
    if len(values) != len(members) or any(value != wanted for value in values):
        raise ValueError(f"archive {field} mismatch")
print(f"PASS: all {len(members)} Alembic archive members are AArch64 ELF64 REL")
PY
}

check_architecture() {
  local binary
  for binary in "${shared}" "${smoke}"; do
    "${CARLA_LLVM_BIN}/llvm-readelf" -h -d "${binary}" > "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Class:[[:space:]]+ELF64' "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Machine:[[:space:]]+AArch64' "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Type:[[:space:]]+DYN' "${run_dir}/$(basename "${binary}").readelf.txt"
    if grep -Eq 'TEXTREL|libstdc\+\+|libhdf5|libpython' "${run_dir}/$(basename "${binary}").readelf.txt"; then
      echo "unexpected text relocation or runtime dependency: ${binary}" >&2
      return 1
    fi
    file "${binary}"
  done
}

check_linkage() {
  ldd -r "${shared}" > "${run_dir}/shared-linkage.txt" 2>&1
  ldd -r "${smoke}" > "${run_dir}/smoke-linkage.txt" 2>&1
  if grep -E 'not found|undefined symbol|libstdc\+\+|libhdf5|libpython' \
    "${run_dir}/shared-linkage.txt" "${run_dir}/smoke-linkage.txt"; then
    return 1
  fi
  cat "${run_dir}/shared-linkage.txt" "${run_dir}/smoke-linkage.txt"
}

check_rejection() {
  python3 - "${smoke}" "${run_dir}/outputs/mesh.abc" <<'PY'
from pathlib import Path
import subprocess
import sys
smoke, archive = sys.argv[1], Path(sys.argv[2])
original = archive.read_bytes()
bad = archive.with_name("truncated.abc")
bad.write_bytes(original[:16])
for mode, path in (("read", bad), ("read", archive.with_name("missing.abc")), ("write", archive)):
    result = subprocess.run([smoke, mode, str(path)], capture_output=True, text=True, timeout=30)
    print(f"{mode} {path.name}: exit={result.returncode}\n{result.stdout}{result.stderr}")
    if result.returncode != 2:
        raise ValueError("expected explicit archive rejection, not success or a crash")
if archive.read_bytes() != original:
    raise ValueError("negative smoke modified the original Ogawa archive")
print("PASS: truncated/missing archive and overwrite rejected")
PY
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
        if stat.S_ISDIR(path.lstat().st_mode):
            continue
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError(f"nonregular source: {path}")
        actual[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"source changed during build: {root}")
print("PASS: original and retained Alembic sources unchanged")
PY
  (cd "${prefix}" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "${run_dir}/installed.sha256"
}

worker() {
  run_dir="$1"
  prefix="${run_dir}/install"
  archive="${prefix}/lib/libAlembic.a"
  shared="${prefix}/lib/libalembic-smoke.so"
  smoke="${prefix}/bin/alembic-smoke"
  run_step imath-before verify_imath before
  imath_prefix="$(< "${run_dir}/imath-prefix.txt")"
  run_step preflight preflight
  run_step source retain_source
  run_step configure cmake -S "${run_dir}/source" -B "${run_dir}/work" -G Ninja \
    --toolchain "${run_dir}/inputs/ue-arm64-third-party.cmake" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="${prefix}" \
    -DALEMBIC_LIB_INSTALL_DIR="${prefix}/lib" -DConfigPackageLocation=lib/cmake/Alembic \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DImath_DIR="${imath_prefix}/lib/cmake/Imath" -DCMAKE_FIND_ROOT_PATH="${imath_prefix}" \
    -DCMAKE_FIND_PACKAGE_PREFER_CONFIG=ON -DCMAKE_DISABLE_FIND_PACKAGE_IlmBase=ON \
    -DALEMBIC_SHARED_LIBS=OFF -DALEMBIC_ILMBASE_LINK_STATIC=ON \
    -DUSE_HDF5=OFF -DUSE_PYALEMBIC=OFF -DUSE_TESTS=OFF -DUSE_BINARIES=OFF \
    -DUSE_EXAMPLES=OFF -DDOCS_PATH=OFF
  run_step configuration check_configuration
  run_step build cmake --build "${run_dir}/work" --parallel "${jobs}" --verbose
  run_step install cmake --install "${run_dir}/work"
  run_step archive check_archive
  local sysroot
  sysroot="$(< "${run_dir}/sysroot.txt")"
  common=("${CARLA_LLVM_BIN}/clang++" "--target=${triple}" "--sysroot=${sysroot}"
    -std=c++14 -fPIC -fuse-ld=lld -stdlib=libc++ -nostdinc++
    -isystem "${libcxx}/include" -isystem "${libcxx}/include/c++/v1"
    -I"${prefix}/include" -I"${imath_prefix}/include" -I"${imath_prefix}/include/Imath"
    "-L${libcxx_lib}")
  runtime=("${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a" -lm -lpthread -ldl)
  run_step smoke-library "${common[@]}" -shared -Wl,-z,defs -Wl,-z,text \
    -Wl,--exclude-libs,ALL -Wl,-soname,libalembic-smoke.so \
    "${run_dir}/inputs/alembic-smoke.cpp" -Wl,--whole-archive "${archive}" \
    -Wl,--no-whole-archive "${imath_prefix}/lib/libImath-3_1.a" "${runtime[@]}" -o "${shared}"
  mkdir -p "${prefix}/bin" "${run_dir}/outputs"
  run_step smoke-compile "${common[@]}" -DALEMBIC_SMOKE_DRIVER \
    "${run_dir}/inputs/alembic-smoke.cpp" "-L${prefix}/lib" -lalembic-smoke \
    '-Wl,-rpath,$ORIGIN/../lib' "${runtime[@]}" -o "${smoke}"
  run_step architecture check_architecture
  run_step linkage check_linkage
  run_step ogawa-write "${smoke}" write "${run_dir}/outputs/mesh.abc"
  run_step ogawa-read "${smoke}" read "${run_dir}/outputs/mesh.abc"
  grep -Fxq 'Alembic 1.8.6 Ogawa read PASS vertices=4 faces=2 samples=2' "${run_dir}/ogawa-read.log"
  run_step rejection check_rejection
  run_step imath-after verify_imath after
  run_step retention check_retention
}

if [[ "${1:-}" == --worker && "$#" == 2 ]]; then
  worker "$2"
  exit 0
fi
[[ "$#" == 0 ]] || { echo "Configure this command through CARLA_* variables, not arguments" >&2; exit 64; }
artifact_root="${CARLA_ARTIFACT_DIR:-/artifacts/carla}/usd"
python3 - "${artifact_root}" "${CARLA_UE_DIR}" "${source_dir}" "${CARLA_SOURCE_DIR:-/workspace/carla}" "$(dirname "${imath_report}")" <<'PY'
from pathlib import Path
import sys
output, *sources = [Path(p).resolve() for p in sys.argv[1:]]
if any(output.is_relative_to(source) for source in sources):
    raise ValueError("refusing to write Alembic artifacts into a source or Imath tree")
PY
mkdir -p "${artifact_root}"
run_dir="$(mktemp -d "${artifact_root}/alembic-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
mkdir "${run_dir}/inputs" "${run_dir}/passed"
printf 'alembic artifacts=%s\n' "${run_dir}"
command=(timeout --kill-after=15s "${timeout_seconds}s" bash "${BASH_SOURCE[0]}" --worker "${run_dir}")
printf '%q ' "${command[@]}" > "${run_dir}/worker.command.txt"
printf '\n' >> "${run_dir}/worker.command.txt"
code=0
"${command[@]}" > "${run_dir}/worker.log" 2>&1 || code=$?
printf '%s\n' "${code}" > "${run_dir}/exit-code.txt"
tail -n 35 "${run_dir}/worker.log"

python3 - "${run_dir}" "${script_dir}/../stage_report.py" "${code}" "${CARLA_UE_DIR}" "${BASH_SOURCE[0]}" "${imath_report}" <<'PY'
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

run, reporter, code, ue, recipe, imath = sys.argv[1:]
run, code = Path(run), int(code)
spec = importlib.util.spec_from_file_location("stage_report", reporter)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
stage_id = "carla-alembic-native-arm64"
scope = "Native ARM64 Alembic 1.8.6 Release static PIC library and Ogawa write/read; not HDF5, Python, OpenUSD or UE Editor"
imath_scope = "Native ARM64 Imath 3.1.9 Release static PIC library and smoke; not PyImath, OpenUSD or UE Editor"
names = ("imath-before", "preflight", "source", "configure", "configuration", "build", "install",
         "archive", "smoke-library", "smoke-compile", "architecture", "linkage", "ogawa-write",
         "ogawa-read", "rejection", "imath-after", "retention", "worker-exit")
checks = {name: "PASS" if (run / "passed" / name).is_file() else "FAIL" for name in names}
checks["worker-exit"] = "PASS" if code == 0 else "FAIL"
try:
    pin = json.loads((run / "imath-pin.json").read_text())
    if hashlib.sha256(Path(imath).read_bytes()).hexdigest() != pin["sha256"]:
        raise ValueError("Imath report changed before finalization")
except (OSError, ValueError, KeyError):
    checks["imath-after"] = "FAIL"
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
source_hash = run / "source.sha256.json"
revision = hashlib.sha256(source_hash.read_bytes()).hexdigest() if source_hash.is_file() else "unverified"
commit = run / "ue-commit.txt"
report = stage.write_report(
    run / "stage-report.json", stage_id=stage_id, scope=scope, exit_code=code,
    required_checks=list(names), checks=checks, evidence=evidence,
    sources={"alembic": {"location": str(run / "source"), "revision": revision},
             "ue": {"location": ue, "revision": commit.read_text().strip() if commit.is_file() else "unverified"}},
    prerequisites=[{"stage_id": "carla-imath-native-arm64", "scope": imath_scope, "path": imath}],
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
