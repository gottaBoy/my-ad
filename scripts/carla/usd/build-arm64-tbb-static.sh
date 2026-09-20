#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-4}"
timeout_seconds="${CARLA_TBB_STATIC_TIMEOUT_SECONDS:-600}"
[[ "${jobs}" =~ ^[1-4]$ ]] || { echo "CARLA_BUILD_JOBS must be 1..4" >&2; exit 64; }
[[ "${timeout_seconds}" =~ ^[1-9][0-9]{0,3}$ && "${timeout_seconds}" -le 1200 ]] || {
  echo "CARLA_TBB_STATIC_TIMEOUT_SECONDS must be 1..1200" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run in native ARM64 carla-dev Docker" >&2; exit 2;
}
export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH LIBRARY_PATH
unset CC CXX MAKEFLAGS MFLAGS LD_LIBRARY_PATH LD_PRELOAD TBBROOT TBB_DIR
export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}"
export CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="${CARLA_UE_DIR}/Engine/Source/ThirdParty/Intel/TBB/IntelTBB-2019u8"
build_rules="${CARLA_UE_DIR}/Engine/Source/ThirdParty/Intel/TBB/IntelTBB.Build.cs"
usd_build="${CARLA_UE_DIR}/Engine/Plugins/Runtime/USDCore/Source/ThirdParty/USD/BuildForLinux.sh"
triple=aarch64-unknown-linux-gnueabi
libcxx="${CARLA_UE_DIR}/Engine/Source/ThirdParty/Unix/LibCxx"
libcxx_lib="${libcxx}/lib/Unix/${triple}"

check_output() {
  python3 -B - "$1" "${CARLA_UE_DIR}" "${CARLA_SOURCE_DIR:-/workspace/carla}" <<'PY'
from pathlib import Path
import sys
output, *sources = [Path(p).resolve() for p in sys.argv[1:]]
if any(output.is_relative_to(p) for p in sources):
    raise ValueError("refusing to write static TBB artifacts into a source tree")
PY
}

run_step() {
  local step="$1" code
  shift
  printf '%q ' "$@" > "${run_dir}/${step}.command.txt"
  printf '\n' >> "${run_dir}/${step}.command.txt"
  printf 'step=%s\n' "${step}"
  set +e
  ( set -e; "$@" ) > "${run_dir}/${step}.log" 2>&1
  code=$?
  set -e
  printf '%s\n' "${code}" > "${run_dir}/${step}.exit-code.txt"
  tail -n 12 "${run_dir}/${step}.log"
  [[ "${code}" == 0 ]] || return "${code}"
  touch "${run_dir}/passed/${step}"
}

preflight() {
  printf 'docker=yes\nhost=%s\ntarget=%s\n' "$(uname -m)" "${triple}"
  local name
  for name in Makefile LICENSE build/Makefile.tbb build/Makefile.tbbmalloc build/linux.clang.inc \
    src/tbb/scheduler.cpp src/tbbmalloc/frontend.cpp include/tbb/tbb_stddef.h; do
    [[ -s "${source_dir}/${name}" ]] || { echo "missing TBB source: ${name}" >&2; return 2; }
  done
  grep -Eq '^#define TBB_VERSION_MAJOR +2019$' "${source_dir}/include/tbb/tbb_stddef.h"
  grep -Eq '^#define TBB_INTERFACE_VERSION +11008$' "${source_dir}/include/tbb/tbb_stddef.h"
  grep -Fq '"libtbb.a"' "${build_rules}"
  grep -Fq '"libtbbmalloc.a"' "${build_rules}"
  grep -Fq 'TBB_USE_EXCEPTIONS=0' "${build_rules}"
  [[ -s "${usd_build}" ]] || { echo "missing USD build contract" >&2; return 2; }
  command -v make
  command -v patch
  for name in clang clang++ llvm-ar llvm-ranlib llvm-readelf llvm-nm ld.lld; do
    [[ -x "${CARLA_LLVM_BIN}/${name}" ]] || { echo "missing native LLVM tool: ${name}" >&2; return 2; }
  done
  [[ -s "${libcxx}/include/c++/v1/__config" && -s "${libcxx_lib}/libc++.a" \
     && -s "${libcxx_lib}/libc++abi.a" ]] || { echo "missing UE ARM64 libc++" >&2; return 2; }
  shopt -s nullglob
  sysroots=("${CARLA_UE_DIR}"/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v*_clang-*/"${triple}")
  [[ "${#sysroots[@]}" == 1 ]] || { echo "expected one UE ARM64 sysroot" >&2; return 2; }
  printf '%s\n' "${sysroots[0]}" > "${run_dir}/sysroot.txt"
  "${CARLA_LLVM_BIN}/clang++" --version
  "${CARLA_LLVM_BIN}/clang++" --target="${triple}" --sysroot="${sysroots[0]}" -dumpmachine
  git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
}

retain_source() {
  cp "${BASH_SOURCE[0]}" "${script_dir}/tbb-static-smoke.cpp" "${script_dir}/../stage_report.py" "${run_dir}/inputs/"
  cp "${build_rules}" "${run_dir}/inputs/IntelTBB.Build.cs"
  cp "${usd_build}" "${run_dir}/inputs/USD-BuildForLinux.sh"
  git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" diff --binary HEAD -- \
    Engine/Source/ThirdParty/Intel/TBB > "${run_dir}/ue-tbb.patch"
  sha256sum "${BASH_SOURCE[0]}" "${script_dir}/tbb-static-smoke.cpp" \
    "${script_dir}/../stage_report.py" "${build_rules}" "${usd_build}" > "${run_dir}/inputs.sha256"
  sha256sum "${CARLA_LLVM_BIN}/clang" "${CARLA_LLVM_BIN}/clang++" \
    "${CARLA_LLVM_BIN}/llvm-ar" "${CARLA_LLVM_BIN}/llvm-ranlib" \
    "${CARLA_LLVM_BIN}/llvm-readelf" "${CARLA_LLVM_BIN}/llvm-nm" "${CARLA_LLVM_BIN}/ld.lld" \
    "${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a" > "${run_dir}/toolchain.sha256"
  python3 -B - "${source_dir}" "${run_dir}" <<'PY'
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import sys
source, run = map(Path, sys.argv[1:])
# Only these trees are build inputs. UE lib/ contains prebuilts and is not copied.
selected = ("src", "include", "build", "Makefile", "LICENSE", "README")
records = {}
for name in selected:
    base = source / name
    for path in ([base] if base.is_file() else sorted(base.rglob("*"))):
        if path.is_symlink():
            raise ValueError(f"source symlink: {path}")
        if path.is_dir():
            continue
        if not path.is_file() or path.suffix in (".o", ".a", ".so", ".dll", ".obj", ".lib"):
            raise ValueError(f"not clean source: {path}")
        relative = path.relative_to(source)
        records[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        destination = run / "source" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
if not records:
    raise ValueError("empty TBB source")
(run / "source.sha256.json").write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
shutil.copytree(run / "source", run / "build-source")
patch = []
for kind, objects in (("tbb", "TBB.OBJ"), ("tbbmalloc", "MALLOC.OBJ")):
    relative = f"build/Makefile.{kind}"
    before = (run / "source" / relative).read_text()
    if f"{objects} " not in before and f"{objects}=" not in before:
        raise ValueError(f"missing original object list: {objects}")
    after = before + f"""
# Project-owned static target: preserve the upstream compilation/object rules.
.PHONY: carla-static
carla-static: lib{kind}.a
lib{kind}.a: $({objects})
\t$(AR) rcsD $@ $({objects})
\tprintf '%s\\n' $({objects}) > {kind}.objects.list
"""
    patch.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                     fromfile="a/" + relative, tofile="b/" + relative))
(run / "static-targets.patch").write_text("".join(patch))
print(f"Retained {len(records)} source files from {selected}; no prebuilt lib/ input")
PY
}

snapshot_patched() {
  python3 -B - "${run_dir}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
run = Path(sys.argv[1])
records = {p.relative_to(run / "build-source").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted((run / "build-source").rglob("*")) if p.is_file()}
(run / "patched-source.sha256.json").write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
print(f"Recorded patched source identity for {len(records)} files")
PY
}

stage_archives() {
  mkdir -p "${prefix}/lib" "${prefix}/include"
  install -m 0644 "${run_dir}/work/tbb/libtbb.a" "${prefix}/lib/libtbb.a"
  install -m 0644 "${run_dir}/work/tbbmalloc/libtbbmalloc.a" "${prefix}/lib/libtbbmalloc.a"
  cp -a "${run_dir}/source/include/." "${prefix}/include/"
  cmp "${run_dir}/work/tbb/libtbb.a" "${prefix}/lib/libtbb.a"
  cmp "${run_dir}/work/tbbmalloc/libtbbmalloc.a" "${prefix}/lib/libtbbmalloc.a"
}

check_archives() {
  python3 -B - "${run_dir}" "${CARLA_LLVM_BIN}" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
run, tools = map(Path, sys.argv[1:])
records = {}
for kind in ("tbb", "tbbmalloc"):
    archive = run / "install/lib" / f"lib{kind}.a"
    work = run / "work" / kind
    expected = (work / f"{kind}.objects.list").read_text().splitlines()
    if not expected or len(expected) != len(set(expected)):
        raise ValueError(f"empty/duplicate build object list: {kind}")
    if archive.read_bytes()[:8] != b"!<arch>\n":
        raise ValueError(f"not a regular (non-thin) archive: {archive}")
    members = subprocess.check_output([str(tools / "llvm-ar"), "t", str(archive)], text=True).splitlines()
    if members != expected:
        raise ValueError(f"archive members differ from upstream object list: {kind}")
    headers = subprocess.check_output([str(tools / "llvm-readelf"), "-h", str(archive)], text=True)
    (run / f"{kind}.archive-readelf.txt").write_text(headers)
    (run / f"{kind}.archive-members.txt").write_text("\n".join(members) + "\n")
    for field, wanted in (("Class", "ELF64"), ("Type", "REL"), ("Machine", "AArch64")):
        values = re.findall(r"^\s*" + field + r":\s+(\S+)", headers, re.M)
        if len(values) != len(members) or any(v != wanted for v in values):
            raise ValueError(f"{kind}: invalid member {field}")
    files = {}
    for member in members:
        if Path(member).name != member or not member.endswith(".o"):
            raise ValueError(f"unexpected archive member: {member}")
        data = subprocess.check_output([str(tools / "llvm-ar"), "p", str(archive), member])
        if len(data) < 20 or data[:6] != b"\x7fELF\x02\x01" or data[16:20] != b"\x01\x00\xb7\x00":
            raise ValueError(f"not an AArch64 ELF64 relocatable object: {member}")
        if data != (work / member).read_bytes():
            raise ValueError(f"archive member is not this run's compiled object: {member}")
        files[member] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                         "object": (work / member).relative_to(run).as_posix()}
    records[kind] = {"archive": archive.relative_to(run).as_posix(),
                     "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "members": files}
    print(f"PASS lib{kind}.a: {len(files)} native REL members match freshly compiled objects")
(run / "archive-objects.json").write_text(json.dumps(records, sort_keys=True, indent=2) + "\n")
PY
}

check_binaries() {
  local binary name
  for name in bin/tbb-static-smoke lib/libtbb-static-smoke.so bin/tbb-wholearchive-smoke; do
    binary="${prefix}/${name}"
    "${CARLA_LLVM_BIN}/llvm-readelf" -h -d "${binary}" > "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Class:[[:space:]]+ELF64' "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq 'Machine:[[:space:]]+AArch64' "${run_dir}/$(basename "${binary}").readelf.txt"
    if grep -Eq 'TEXTREL|NEEDED.*(libtbb\.so|libtbbmalloc\.so|libstdc\+\+)' "${run_dir}/$(basename "${binary}").readelf.txt"; then
      echo "unexpected dynamic TBB/libstdc++ dependency or TEXTREL" >&2; return 1
    fi
    ldd -r "${binary}" > "${run_dir}/$(basename "${binary}").ldd.txt" 2>&1
    if grep -Eq 'not found|undefined symbol|libtbb\.so|libtbbmalloc\.so|libstdc\+\+' "${run_dir}/$(basename "${binary}").ldd.txt"; then
      echo "linkage check failed: ${binary}" >&2; return 1
    fi
    file "${binary}"
  done
}

retention() {
  sha256sum --check "${run_dir}/inputs.sha256"
  sha256sum --check "${run_dir}/toolchain.sha256"
  python3 -B - "${source_dir}" "${run_dir}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
source, run = map(Path, sys.argv[1:])
def snapshot(root, selected=None):
    paths = []
    for name in selected or (".",):
        base = root / name
        paths.extend([base] if base.is_file() else base.rglob("*"))
    result = {}
    for p in sorted(paths):
        if p.is_symlink():
            raise ValueError(f"source became a symlink: {p}")
        if p.is_dir():
            continue
        if not p.is_file():
            raise ValueError(f"nonregular source: {p}")
        result[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result
diffs = {}
for label, root, manifest, selected in (
    ("original", source, "source.sha256.json", ("src", "include", "build", "Makefile", "LICENSE", "README")),
    ("source", run / "source", "source.sha256.json", None),
    ("patched", run / "build-source", "patched-source.sha256.json", None),
):
    before = json.loads((run / manifest).read_text())
    after = snapshot(root, selected)
    (run / f"{label}.after.sha256.json").write_text(json.dumps(after, sort_keys=True, indent=2) + "\n")
    diffs[label] = {
        "added": {p: after[p] for p in sorted(after.keys() - before.keys())},
        "removed": {p: before[p] for p in sorted(before.keys() - after.keys())},
        "changed": {p: {"before": before[p], "after": after[p]}
                    for p in sorted(before.keys() & after.keys()) if before[p] != after[p]},
    }
(run / "source-retention-diff.json").write_text(json.dumps(diffs, sort_keys=True, indent=2) + "\n")
if any(change for diff in diffs.values() for change in diff.values()):
    raise ValueError("TBB source changed during build; see source-retention-diff.json")
print("PASS original, pristine and patched source sets unchanged")
PY
  (cd "${prefix}" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "${run_dir}/installed.sha256"
}

worker() {
  run_dir="$1"
  check_output "${run_dir}"
  [[ -d "${run_dir}/inputs" && -d "${run_dir}/passed" \
    && ! -e "${run_dir}/source" && ! -e "${run_dir}/work" && ! -e "${run_dir}/install" ]] || {
    echo "fresh supervisor directory required" >&2; return 2;
  }
  prefix="${run_dir}/install"
  run_step preflight preflight
  run_step source retain_source
  run_step patch patch --batch --fuzz=0 -p1 -d "${run_dir}/build-source" -i "${run_dir}/static-targets.patch"
  run_step patched-source snapshot_patched
  local sysroot flags
  sysroot="$(< "${run_dir}/sysroot.txt")"
  flags="-std=c++11 -fPIC -fno-rtti -fno-exceptions -DTBB_USE_EXCEPTIONS=0 -D__TBB_DYNAMIC_LOAD_ENABLED=0"
  flags+=" -stdlib=libc++ -nostdinc++ -isystem ${libcxx}/include -isystem ${libcxx}/include/c++/v1"
  mkdir -p "${run_dir}/work/tbb" "${run_dir}/work/tbbmalloc"
  make_args=(-r -j"${jobs}" "tbb_root=${run_dir}/build-source" cfg=release
    compiler=clang arch=aarch64 runtime=ue-libcxx-arm64-static tbb_os=linux
    "CPLUS=${CARLA_LLVM_BIN}/clang++ --target=${triple} --sysroot=${sysroot}"
    "CONLY=${CARLA_LLVM_BIN}/clang --target=${triple} --sysroot=${sysroot}"
    "AR=${CARLA_LLVM_BIN}/llvm-ar" "CXXFLAGS=${flags}" exceptions=0)
  run_step build-tbb make -C "${run_dir}/work/tbb" -f "${run_dir}/build-source/build/Makefile.tbb" \
    "${make_args[@]}" carla-static
  run_step build-tbbmalloc make -C "${run_dir}/work/tbbmalloc" -f "${run_dir}/build-source/build/Makefile.tbbmalloc" \
    "${make_args[@]}" carla-static
  run_step install stage_archives
  run_step archives check_archives
  mkdir -p "${prefix}/bin"
  compiler=("${CARLA_LLVM_BIN}/clang++" "--target=${triple}" "--sysroot=${sysroot}"
    -std=c++11 -fPIC -fno-rtti -fno-exceptions -DTBB_USE_EXCEPTIONS=0
    -D__TBB_DYNAMIC_LOAD_ENABLED=0 -stdlib=libc++ -nostdinc++ -fuse-ld=lld
    -isystem "${libcxx}/include" -isystem "${libcxx}/include/c++/v1"
    "-I${prefix}/include" "-L${libcxx_lib}")
  runtime=("${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a" -lm -lpthread -ldl -lrt)
  archives=("${prefix}/lib/libtbb.a" "${prefix}/lib/libtbbmalloc.a")
  run_step smoke-static-link "${compiler[@]}" "${run_dir}/inputs/tbb-static-smoke.cpp" \
    "${archives[@]}" "${runtime[@]}" "-Wl,-Map,${run_dir}/static-link.map" \
    -o "${prefix}/bin/tbb-static-smoke"
  run_step wholearchive-link "${compiler[@]}" -DTBB_STATIC_SMOKE_LIBRARY \
    "${run_dir}/inputs/tbb-static-smoke.cpp" -shared -Wl,-z,defs -Wl,-z,text \
    -Wl,--exclude-libs,ALL -Wl,-soname,libtbb-static-smoke.so \
    -Wl,--whole-archive "${archives[@]}" -Wl,--no-whole-archive "${runtime[@]}" \
    "-Wl,-Map,${run_dir}/wholearchive-link.map" -o "${prefix}/lib/libtbb-static-smoke.so"
  run_step smoke-driver-link "${compiler[@]}" -DTBB_STATIC_SMOKE_DRIVER \
    "${run_dir}/inputs/tbb-static-smoke.cpp" "-L${prefix}/lib" -ltbb-static-smoke \
    '-Wl,-rpath,$ORIGIN/../lib' "${runtime[@]}" -o "${prefix}/bin/tbb-wholearchive-smoke"
  run_step architecture-linkage check_binaries
  run_step smoke-static timeout 60 "${prefix}/bin/tbb-static-smoke"
  run_step smoke-wholearchive timeout 60 "${prefix}/bin/tbb-wholearchive-smoke"
  run_step retention retention
}

if [[ "${1:-}" == --worker && "$#" == 2 ]]; then
  worker "$2"
  exit 0
fi
[[ "$#" == 0 ]] || { echo "use CARLA_* environment variables only" >&2; exit 64; }
artifact_root="${CARLA_ARTIFACT_DIR:-/artifacts/carla}/usd"
check_output "${artifact_root}"
mkdir -p "${artifact_root}"
run_dir="$(mktemp -d "${artifact_root}/tbb-static-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
mkdir "${run_dir}/inputs" "${run_dir}/passed"
printf 'tbb-static artifacts=%s\n' "${run_dir}"
command=(timeout --kill-after=15s "${timeout_seconds}s" bash "${BASH_SOURCE[0]}" --worker "${run_dir}")
printf '%q ' "${command[@]}" > "${run_dir}/worker.command.txt"
printf '\n' >> "${run_dir}/worker.command.txt"
code=0
"${command[@]}" > "${run_dir}/worker.log" 2>&1 || code=$?
printf '%s\n' "${code}" > "${run_dir}/exit-code.txt"
tail -n 35 "${run_dir}/worker.log"
python3 -B - "${run_dir}" "${script_dir}/../stage_report.py" "${code}" "${CARLA_UE_DIR}" "${BASH_SOURCE[0]}" <<'PY'
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
run, reporter, code, ue, recipe = sys.argv[1:]
run, code = Path(run), int(code)
spec = importlib.util.spec_from_file_location("tbb_static_reporter", reporter)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
stage_id = "carla-tbb-static-native-arm64"
scope = ("Native ARM64 TBB 2019u8 Release libtbb.a/libtbbmalloc.a PIC, no-exceptions, "
         "direct/static and whole-archive smoke ONLY; not Debug, shared coexistence, OpenUSD or UE Editor")
names = ("preflight", "source", "patch", "patched-source", "build-tbb", "build-tbbmalloc",
         "install", "archives", "smoke-static-link", "wholearchive-link", "smoke-driver-link",
         "architecture-linkage", "smoke-static", "smoke-wholearchive", "retention", "worker-exit")
checks = {n: "PASS" if (run / "passed" / n).is_file() else "FAIL" for n in names}
checks["worker-exit"] = "PASS" if code == 0 else "FAIL"
evidence = {n: run / (n + ".log") if (run / (n + ".log")).is_file() else run / "worker.log" for n in names}
evidence["worker-exit"] = run / "exit-code.txt"
for path in sorted(run.rglob("*")):
    if path.is_file() and path.relative_to(run).parts[0] != "passed":
        evidence["retained." + path.relative_to(run).as_posix()] = path
identity = run / "patched-source.sha256.json"
commit = run / "ue-commit.txt"
report = stage.write_report(
    run / "stage-report.json", stage_id=stage_id, scope=scope, exit_code=code,
    required_checks=list(names), checks=checks, evidence=evidence,
    sources={"tbb": {"location": str(run / "build-source"),
                     "revision": hashlib.sha256(identity.read_bytes()).hexdigest() if identity.is_file() else "unverified"},
             "ue": {"location": ue, "revision": commit.read_text().strip() if commit.is_file() else "unverified"}},
    command=["bash", recipe])
(run / "decision.json").write_text(json.dumps(
    {"status": report["status"], "exit_code": code, "scope": scope, "checks": checks}, indent=2) + "\n")
if report["status"] == "PASS":
    stage.validate_report(run / "stage-report.json", stage_id=stage_id, scope=scope)
print(f"{report['status']} {stage_id} report={run / 'stage-report.json'} exit={code}")
sys.exit(0 if report["status"] == "PASS" else 1)
PY
