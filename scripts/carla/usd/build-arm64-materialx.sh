#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-4}"
timeout_seconds="${CARLA_MATERIALX_TIMEOUT_SECONDS:-1200}"
[[ "${jobs}" =~ ^[1-4]$ ]] || { echo "CARLA_BUILD_JOBS must be an integer from 1 to 4" >&2; exit 64; }
[[ "${timeout_seconds}" =~ ^[1-9][0-9]{0,3}$ && "${timeout_seconds}" -le 1200 ]] || {
  echo "CARLA_MATERIALX_TIMEOUT_SECONDS must be an integer from 1 to 1200" >&2
  exit 64
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run this script in the native ARM64 carla-dev container" >&2
  exit 2
}
export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}"
export CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
unset CC CXX CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CPATH CPLUS_INCLUDE_PATH C_INCLUDE_PATH
unset LIBRARY_PATH MAKEFLAGS CMAKE_PREFIX_PATH LD_LIBRARY_PATH LD_PRELOAD
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
module="${CARLA_UE_DIR}/Engine/Source/ThirdParty/MaterialX"
source_dir="${CARLA_MATERIALX_SOURCE_DIR:-${module}/MaterialX-1.38.5}"
usd_rule="${CARLA_UE_DIR}/Engine/Plugins/Runtime/USDCore/Source/ThirdParty/USD/BuildForLinux.sh"
triple=aarch64-unknown-linux-gnueabi
libcxx="${CARLA_UE_DIR}/Engine/Source/ThirdParty/Unix/LibCxx"
libcxx_lib="${libcxx}/lib/Unix/${triple}"
libraries=(MaterialXCore MaterialXFormat MaterialXGenGlsl MaterialXGenMdl MaterialXGenOsl MaterialXGenShader)

run_step() {
  local step="$1"
  shift
  printf '%q ' "$@" > "${run_dir}/${step}.command.txt"
  printf '\n' >> "${run_dir}/${step}.command.txt"
  printf 'step=%s\n' "${step}"
  # Keep function calls out of conditionals so errexit remains active.
  ( "$@" ) > "${run_dir}/${step}.log" 2>&1
  touch "${run_dir}/passed/${step}"
  tail -n 8 "${run_dir}/${step}.log"
}

preflight() {
  printf 'container=docker\nhost=%s\ntarget=%s\n' "$(uname -m)" "${triple}"
  [[ -s "${source_dir}/CMakeLists.txt" ]] || { echo "missing MaterialX source: ${source_dir}/CMakeLists.txt" >&2; return 2; }
  python3 - "${source_dir}/CMakeLists.txt" "${usd_rule}" "${module}/MaterialX.Build.cs" "${libraries[@]}" <<'PY'
from pathlib import Path
import re
import sys
source, usd, rule = [Path(p).read_text() for p in sys.argv[1:4]]
version = dict(re.findall(r"(?m)^set\(MATERIALX_(MAJOR|MINOR|BUILD)_VERSION\s+(\d+)\)", source))
if version != {"MAJOR": "1", "MINOR": "38", "BUILD": "5"}:
    raise ValueError(f"MaterialX source version must be 1.38.5, found {version}")
if not re.search(r'(?m)^MATERIALX_LOCATION=.*?/MaterialX-1\.38\.5"', usd):
    raise ValueError("USD BuildForLinux.sh no longer selects MaterialX-1.38.5")
match = re.search(r'MaterialXLibraries\s*=\s*new\s+string\[\]\s*\{([^}]+)\}', rule)
if not match or sorted(re.findall(r'"([^"]+)"', match[1])) != sorted(sys.argv[4:]):
    raise ValueError("MaterialX Linux library requirements changed")
print("PASS: USD requires 1.38.5; source version and six Linux libraries match")
PY
  local path name
  for path in source/MaterialXCore/Generated.h.in cmake/modules/MaterialXConfig.cmake.in \
    source/MaterialXCore/Document.cpp source/MaterialXFormat/XmlIo.cpp \
    libraries/CMakeLists.txt libraries/stdlib/stdlib_defs.mtlx libraries/bxdf/standard_surface.mtlx; do
    [[ -s "${source_dir}/${path}" ]] || { echo "missing MaterialX source: ${source_dir}/${path}" >&2; return 2; }
  done
  for name in "${libraries[@]}"; do
    [[ -s "${source_dir}/source/${name}/CMakeLists.txt" ]] || { echo "missing library source: ${name}" >&2; return 2; }
  done
  for path in clang clang++ llvm-ar llvm-ranlib llvm-readelf ld.lld; do
    [[ -x "${CARLA_LLVM_BIN}/${path}" ]] || { echo "missing native LLVM tool: ${path}" >&2; return 2; }
  done
  for path in "${libcxx}/include/c++/v1/__config" "${libcxx_lib}/libc++.a" \
    "${libcxx_lib}/libc++abi.a" "${module}/BuildForLinux.sh"; do
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
}

source_state() {
  python3 - "${source_dir}" "${run_dir}" "$1" <<'PY'
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
source, run, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
def snapshot(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if stat.S_ISDIR(path.lstat().st_mode):
            continue
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError(f"nonregular source input: {path}")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not result:
        raise ValueError(f"empty source: {root}")
    return result
if mode == "capture":
    expected = snapshot(source)
    shutil.copytree(source, run / "source")
    (run / "source.sha256.json").write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
else:
    expected = json.loads((run / "source.sha256.json").read_text())
for root in (source, run / "source"):
    if snapshot(root) != expected:
        raise ValueError(f"MaterialX source changed: {root}")
print(f"PASS: {len(expected)} original/retained MaterialX source files match ({mode})")
PY
}

retain_source() {
  cp "${BASH_SOURCE[0]}" "${run_dir}/inputs/build-arm64-materialx.sh"
  cp "${script_dir}/materialx-smoke.cpp" "${run_dir}/inputs/"
  cp "${script_dir}/../ue-arm64-third-party.cmake" "${script_dir}/../stage_report.py" "${run_dir}/inputs/"
  cp "${module}/MaterialX.Build.cs" "${module}/BuildForLinux.sh" "${run_dir}/inputs/"
  cp "${usd_rule}" "${run_dir}/inputs/USD-BuildForLinux.sh"
  source_state capture
  git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" diff --binary HEAD -- \
    Engine/Source/ThirdParty/MaterialX > "${run_dir}/ue-materialx.patch"
  sha256sum "${CARLA_LLVM_BIN}/clang" "${CARLA_LLVM_BIN}/clang++" "${CARLA_LLVM_BIN}/llvm-ar" \
    "${CARLA_LLVM_BIN}/llvm-ranlib" "${CARLA_LLVM_BIN}/ld.lld" \
    "${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a" > "${run_dir}/toolchain.sha256"
  sha256sum "${BASH_SOURCE[0]}" "${script_dir}/materialx-smoke.cpp" \
    "${script_dir}/../ue-arm64-third-party.cmake" "${script_dir}/../stage_report.py" \
    "${module}/MaterialX.Build.cs" "${module}/BuildForLinux.sh" "${usd_rule}" > "${run_dir}/inputs.sha256"
}

check_configuration() {
  python3 - "${run_dir}/work/CMakeCache.txt" "${run_dir}/work/source/MaterialXCore/Generated.h" <<'PY'
from pathlib import Path
import re
import sys
cache = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if line and not line.startswith(("#", "//")) and "=" in line:
        key, value = line.split("=", 1)
        cache[key.split(":", 1)[0]] = value
for name, value in {
    "CMAKE_BUILD_TYPE": "Release", "CMAKE_POSITION_INDEPENDENT_CODE": "ON",
    "MATERIALX_BUILD_SHARED_LIBS": "OFF", "MATERIALX_BUILD_GEN_GLSL": "ON",
    "MATERIALX_BUILD_GEN_OSL": "ON", "MATERIALX_BUILD_GEN_MDL": "ON",
    "MATERIALX_BUILD_RENDER": "OFF", "MATERIALX_BUILD_TESTS": "OFF",
    "MATERIALX_TEST_RENDER": "OFF", "MATERIALX_BUILD_PYTHON": "OFF",
    "MATERIALX_BUILD_VIEWER": "OFF", "MATERIALX_BUILD_JS": "OFF",
}.items():
    if cache.get(name) != value:
        raise ValueError(f"unexpected configuration: {name}={cache.get(name)}")
version = dict(re.findall(r"(?m)^#define MATERIALX_(MAJOR|MINOR|BUILD)_VERSION\s+(\d+)", Path(sys.argv[2]).read_text()))
if version != {"MAJOR": "1", "MINOR": "38", "BUILD": "5"}:
    raise ValueError(f"generated header version mismatch: {version}")
print("PASS: generated 1.38.5 header, six Release/static/PIC libraries, Render/Python/JS OFF")
PY
}

check_archives() {
  local name
  for name in "${libraries[@]}"; do
    "${CARLA_LLVM_BIN}/llvm-ar" t "${prefix}/lib/lib${name}.a" > "${run_dir}/${name}.members.txt"
    "${CARLA_LLVM_BIN}/llvm-readelf" -h "${prefix}/lib/lib${name}.a" > "${run_dir}/${name}.readelf.txt"
  done
  python3 - "${run_dir}" "${libraries[@]}" <<'PY'
from pathlib import Path
import re
import sys
root = Path(sys.argv[1])
for name in sys.argv[2:]:
    members = (root / (name + ".members.txt")).read_text().splitlines()
    text = (root / (name + ".readelf.txt")).read_text()
    if not members:
        raise ValueError(f"empty archive: {name}")
    for field, wanted in (("Class", "ELF64"), ("Machine", "AArch64"), ("Type", "REL")):
        values = re.findall(r"^\s*" + field + r":\s+(\S+)", text, re.M)
        if len(values) != len(members) or any(value != wanted for value in values):
            raise ValueError(f"{name}: invalid archive {field}")
    print(f"PASS: {name}, {len(members)} AArch64 ELF64 REL members")
PY
}

check_architecture() {
  local binary
  for binary in "${shared}" "${smoke}"; do
    "${CARLA_LLVM_BIN}/llvm-readelf" -h -d "${binary}" > "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Class:[[:space:]]+ELF64' "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Machine:[[:space:]]+AArch64' "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Type:[[:space:]]+DYN' "${run_dir}/$(basename "${binary}").readelf.txt"
    if grep -Eq 'TEXTREL|libstdc\+\+|libGL\.|libpython' "${run_dir}/$(basename "${binary}").readelf.txt"; then
      echo "unexpected runtime dependency or TEXTREL: ${binary}" >&2
      return 1
    fi
    file "${binary}"
  done
}

check_linkage() {
  ldd -r "${shared}" > "${run_dir}/shared-linkage.txt" 2>&1
  ldd -r "${smoke}" > "${run_dir}/smoke-linkage.txt" 2>&1
  if grep -E 'not found|undefined symbol|libstdc\+\+|libGL\.|libpython' \
    "${run_dir}/shared-linkage.txt" "${run_dir}/smoke-linkage.txt"; then
    return 1
  fi
  cat "${run_dir}/shared-linkage.txt" "${run_dir}/smoke-linkage.txt"
}

check_rejection() {
  python3 - "${smoke}" "${run_dir}/outputs/material.mtlx" "${prefix}/libraries" <<'PY'
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
smoke, document, libraries = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
original = document.read_bytes()
malformed = document.with_name("malformed.mtlx")
malformed.write_text("<materialx><")
invalid = document.with_name("invalid-type.mtlx")
tree = ET.fromstring(original)
value = tree.find("./standard_surface[@name='surface']/input[@name='specular_roughness']")
if value is None:
    raise ValueError("smoke XML lacks the expected material input")
value.set("type", "color3")
value.set("value", "0.1, 0.2, 0.3")
invalid.write_bytes(ET.tostring(tree))
for mode, path in (("read", malformed), ("read", invalid),
                   ("read", document.with_name("missing.mtlx")), ("write", document)):
    result = subprocess.run([smoke, mode, str(path), libraries], capture_output=True, text=True, timeout=30)
    print(f"{mode} {path.name}: exit={result.returncode}\n{result.stdout}{result.stderr}")
    if result.returncode != 2:
        raise ValueError("expected explicit rejection, not success or a crash")
    if path == invalid and "validation failed" not in result.stderr:
        raise ValueError("invalid material type was not rejected by Document::validate")
if document.read_bytes() != original:
    raise ValueError("negative smoke modified the control document")
print("PASS: malformed XML, invalid material type, missing file and overwrite rejected")
PY
}

check_retention() {
  sha256sum --check "${run_dir}/toolchain.sha256"
  sha256sum --check "${run_dir}/inputs.sha256"
  source_state verify
  (cd "${prefix}" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "${run_dir}/installed.sha256"
}

worker() {
  run_dir="$1"
  prefix="${run_dir}/install"
  shared="${prefix}/lib/libmaterialx-smoke.so"
  smoke="${prefix}/bin/materialx-smoke"
  run_step preflight preflight
  run_step source retain_source
  run_step configure cmake -S "${run_dir}/source" -B "${run_dir}/work" -G Ninja \
    --toolchain "${run_dir}/inputs/ue-arm64-third-party.cmake" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="${prefix}" \
    -DMATERIALX_INSTALL_INCLUDE_PATH=include -DMATERIALX_INSTALL_LIB_PATH=lib \
    -DMATERIALX_INSTALL_STDLIB_PATH=libraries -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DMATERIALX_BUILD_SHARED_LIBS=OFF \
    -DMATERIALX_BUILD_GEN_GLSL=ON -DMATERIALX_BUILD_GEN_MDL=ON -DMATERIALX_BUILD_GEN_OSL=ON \
    -DMATERIALX_BUILD_RENDER=OFF -DMATERIALX_BUILD_TESTS=OFF -DMATERIALX_TEST_RENDER=OFF \
    -DMATERIALX_BUILD_PYTHON=OFF -DMATERIALX_INSTALL_PYTHON=OFF -DMATERIALX_BUILD_JS=OFF \
    -DMATERIALX_BUILD_VIEWER=OFF -DMATERIALX_BUILD_DOCS=OFF -DMATERIALX_BUILD_OIIO=OFF
  run_step configuration check_configuration
  run_step build cmake --build "${run_dir}/work" --parallel "${jobs}" --verbose
  run_step install cmake --install "${run_dir}/work"
  run_step archives check_archives
  local sysroot name
  sysroot="$(< "${run_dir}/sysroot.txt")"
  archives=()
  for name in "${libraries[@]}"; do archives+=("${prefix}/lib/lib${name}.a"); done
  common=("${CARLA_LLVM_BIN}/clang++" "--target=${triple}" "--sysroot=${sysroot}"
    -std=c++14 -fPIC -fuse-ld=lld -stdlib=libc++ -nostdinc++
    -isystem "${libcxx}/include" -isystem "${libcxx}/include/c++/v1"
    -I"${prefix}/include" "-L${libcxx_lib}")
  runtime=("${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a" -lm -lpthread -ldl)
  run_step smoke-library "${common[@]}" -shared -Wl,-z,defs -Wl,-z,text \
    -Wl,--exclude-libs,ALL -Wl,-soname,libmaterialx-smoke.so \
    "${run_dir}/inputs/materialx-smoke.cpp" -Wl,--whole-archive "${archives[@]}" \
    -Wl,--no-whole-archive "${runtime[@]}" -o "${shared}"
  mkdir -p "${prefix}/bin" "${run_dir}/outputs"
  run_step smoke-compile "${common[@]}" -DMATERIALX_SMOKE_DRIVER \
    "${run_dir}/inputs/materialx-smoke.cpp" "-L${prefix}/lib" -lmaterialx-smoke \
    '-Wl,-rpath,$ORIGIN/../lib' "${runtime[@]}" -o "${smoke}"
  run_step architecture check_architecture
  run_step linkage check_linkage
  run_step xml-write "${smoke}" write "${run_dir}/outputs/material.mtlx" "${prefix}/libraries"
  run_step xml-read "${smoke}" read "${run_dir}/outputs/material.mtlx" "${prefix}/libraries"
  grep -Fxq 'MaterialX 1.38.5 XML read/validate PASS material=material_control shader=standard_surface generators=3' "${run_dir}/xml-read.log"
  run_step rejection check_rejection
  run_step retention check_retention
}

if [[ "${1:-}" == --worker && "$#" == 2 ]]; then
  worker "$2"
  exit 0
fi
[[ "$#" == 0 ]] || { echo "Configure this command through CARLA_* variables, not arguments" >&2; exit 64; }
artifact_root="${CARLA_ARTIFACT_DIR:-/artifacts/carla}/usd"
python3 - "${artifact_root}" "${CARLA_UE_DIR}" "${source_dir}" "${CARLA_SOURCE_DIR:-/workspace/carla}" <<'PY'
from pathlib import Path
import sys
output, *sources = [Path(p).resolve() for p in sys.argv[1:]]
if any(output.is_relative_to(source) for source in sources):
    raise ValueError("refusing to write MaterialX artifacts into a source tree")
PY
mkdir -p "${artifact_root}"
run_dir="$(mktemp -d "${artifact_root}/materialx-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
mkdir "${run_dir}/inputs" "${run_dir}/passed"
printf 'materialx artifacts=%s\n' "${run_dir}"
command=(timeout --kill-after=15s "${timeout_seconds}s" bash "${BASH_SOURCE[0]}" --worker "${run_dir}")
printf '%q ' "${command[@]}" > "${run_dir}/worker.command.txt"
printf '\n' >> "${run_dir}/worker.command.txt"
code=0
"${command[@]}" > "${run_dir}/worker.log" 2>&1 || code=$?
printf '%s\n' "${code}" > "${run_dir}/exit-code.txt"
tail -n 35 "${run_dir}/worker.log"

# Finalization runs outside the bounded worker, so timeout cannot promote partial work.
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
stage_id = "carla-materialx-native-arm64"
scope = "Native ARM64 MaterialX 1.38.5 six Release PIC libraries and XML material validation; not rendering, Python, OpenUSD or UE Editor"
names = ("preflight", "source", "configure", "configuration", "build", "install", "archives",
         "smoke-library", "smoke-compile", "architecture", "linkage", "xml-write", "xml-read",
         "rejection", "retention", "worker-exit")
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
source_hash = run / "source.sha256.json"
revision = hashlib.sha256(source_hash.read_bytes()).hexdigest() if source_hash.is_file() else "unverified"
commit = run / "ue-commit.txt"
report = stage.write_report(
    run / "stage-report.json", stage_id=stage_id, scope=scope, exit_code=code,
    required_checks=list(names), checks=checks, evidence=evidence,
    sources={"materialx": {"location": str(run / "source"), "revision": revision},
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
