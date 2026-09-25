#!/usr/bin/env bash
# Rebuilds the Linux ARM64 third-party archives that block the
# CarlaUnrealEditor dependency-graph link step. Scope: Vorbis, VHACD, libPNG,
# KissFFT, libxml2, FontConfig, metis and FreeImage. They are compiled with the
# clang + sysroot and validated with a whole-archive shared PIC link.
set -Eeuo pipefail
umask 022

if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi
jobs="${CARLA_BUILD_JOBS:-8}"
[[ "${jobs}" =~ ^[1-9][0-9]*$ ]] || { echo "CARLA_BUILD_JOBS must be positive" >&2; exit 64; }

ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
llvm_bin="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
third_party="${ue_dir}/Engine/Source/ThirdParty"
triple=aarch64-unknown-linux-gnueabi
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build_dir="${artifact_dir}/editor-deps-arm64"
mkdir -p "${build_dir}"
run_dir="$(mktemp -d "${artifact_dir}/editor-deps-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight

finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 Editor Third-Party Dependencies\n\n- Status: %s\n- Step: %s\n- Scope: Vorbis, VHACD, libPNG, KissFFT, libxml2, FontConfig, metis and FreeImage ARM64 artifacts only; not Editor/Cook/runtime validation\n" \
    "${status}" "${step}" > "${run_dir}/decision.md"
  printf "%s editor-deps artifacts=%s step=%s\n" "${status}" "${run_dir}" "${step}"
}
trap finish EXIT

run_step() {
  step="$1"
  shift
  printf "step=%s\n" "${step}"
  printf "%q " "$@" > "${run_dir}/${step}.command.txt"
  printf "\n" >> "${run_dir}/${step}.command.txt"
  if "$@" > "${run_dir}/${step}.log" 2>&1; then
    tail -n 5 "${run_dir}/${step}.log"
  else
    local code=$?
    tail -n 60 "${run_dir}/${step}.log"
    return "${code}"
  fi
}

[[ -x "${llvm_bin}/clang" && -x "${llvm_bin}/clang++" && -x "${llvm_bin}/llvm-ar" ]] || {
  echo "LLVM 18 ARM64 tools are missing under ${llvm_bin}" >&2
  exit 2
}
shopt -s nullglob
sysroots=("${ue_dir}"/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v*_clang-*/"${triple}")
[[ "${#sysroots[@]}" == 1 ]] || { echo "Expected exactly one ARM64 UE sysroot" >&2; exit 2; }
sysroot="${sysroots[0]}"
libcxx="${ue_dir}/Engine/Source/ThirdParty/Unix/LibCxx"
[[ -f "${libcxx}/lib/Unix/${triple}/libc++.a" ]] || { echo "UE ARM64 libc++ is missing" >&2; exit 2; }

git_cmd=(git -c "safe.directory=${ue_dir}" -C "${ue_dir}")
"${git_cmd[@]}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
"${git_cmd[@]}" diff --binary HEAD > "${run_dir}/ue-tracked.patch"
sha256sum "${BASH_SOURCE[0]}" > "${run_dir}/scripts.sha256"

cc=("${llvm_bin}/clang" "--target=${triple}" "--sysroot=${sysroot}" -fuse-ld=lld)
cxx=("${llvm_bin}/clang++" "--target=${triple}" "--sysroot=${sysroot}" -fuse-ld=lld)
common_cflags=(-fPIC -O2 -fms-extensions)
common_cxxflags=(-fPIC -O2 -fms-extensions -stdlib=libc++ -nostdinc++
  -isystem "${libcxx}/include" -isystem "${libcxx}/include/c++/v1")
link_libs=("${libcxx}/lib/Unix/${triple}/libc++.a" "${libcxx}/lib/Unix/${triple}/libc++abi.a" -lm -lpthread -ldl)
verify_archive() {
  local archive="$1"
  local report="${run_dir}/$(basename "${archive}").elf.txt"
  "${llvm_bin}/llvm-readelf" -h "${archive}" > "${report}"
  awk '/Machine:/{found=1; if ($2 != "AArch64") bad=1} END{exit !found || bad}' "${report}"
}

install_archive() {
  local archive="$1" destination="$2"
  run_step "install-$(basename "${archive}")" install -D -m 0644 "${archive}" "${destination}"
  verify_archive "${destination}"
  printf '%s  %s\n' "$(sha256sum "${destination}" | awk '{print $1}')" "${destination}" \
    >> "${run_dir}/installed.sha256"
}

# --- Vorbis -------------------------------------------------------------
vorbis_source="${third_party}/Vorbis/libvorbis-1.3.2"
ogg_include="${third_party}/Ogg/libogg-1.2.2/include"
ogg_config_include="${artifact_dir}/audio-deps-arm64/ogg-source/include"
[[ -f "${ogg_config_include}/ogg/config_types.h" ]] || {
  echo "Ogg config_types.h is missing (run build-arm64-audio-deps.sh first)" >&2
  exit 2
}
vorbis_build="${build_dir}/vorbis"
rm -rf "${vorbis_build}"
mkdir -p "${vorbis_build}"

vorbis_flags=("${common_cflags[@]}" -I"${vorbis_source}/include" -I"${ogg_include}" -I"${ogg_config_include}" -I"${vorbis_source}/lib")
vorbis_core=(mdct block floor0 floor1 res0 mapping0 registry codebook sharedbook envelope lpc lsp psy info synthesis window bitrate smallft)
vorbis_enc=(analysis vorbisenc)
vorbis_file=(vorbisfile)

compile_set() {
  local set_name="$1"; shift
  local -n names_ref="$1"; shift
  for name in "${names_ref[@]}"; do
    run_step "vorbis-compile-${set_name}-${name}" "${cc[@]}" "${vorbis_flags[@]}" \
      -c "${vorbis_source}/lib/${name}.c" -o "${vorbis_build}/${name}.o"
  done
}

compile_set core vorbis_core
compile_set enc vorbis_enc
compile_set file vorbis_file

core_objs=()
for name in "${vorbis_core[@]}"; do core_objs+=("${vorbis_build}/${name}.o"); done
enc_objs=()
for name in "${vorbis_enc[@]}"; do enc_objs+=("${vorbis_build}/${name}.o"); done
file_objs=()
for name in "${vorbis_file[@]}"; do file_objs+=("${vorbis_build}/${name}.o"); done

run_step vorbis-archive-core "${llvm_bin}/llvm-ar" rcs "${vorbis_build}/libvorbis.a" "${core_objs[@]}"
run_step vorbis-archive-enc "${llvm_bin}/llvm-ar" rcs "${vorbis_build}/libvorbisenc.a" "${enc_objs[@]}"
run_step vorbis-archive-file "${llvm_bin}/llvm-ar" rcs "${vorbis_build}/libvorbisfile.a" "${file_objs[@]}"

ogg_archive="${artifact_dir}/audio-deps-arm64/ogg/libogg.a"
run_step vorbis-pic-link "${cc[@]}" -shared -Wl,-z,defs -Wl,--whole-archive \
  "${vorbis_build}/libvorbis.a" "${vorbis_build}/libvorbisenc.a" "${vorbis_build}/libvorbisfile.a" \
  "${ogg_archive}" -Wl,--no-whole-archive -lm -o "${run_dir}/libvorbis_probe.so"

# --- VHACD --------------------------------------------------------------
vhacd_source="${third_party}/VHACD"
vhacd_build="${build_dir}/vhacd"
rm -rf "${vhacd_build}"
mkdir -p "${vhacd_build}"

vhacd_flags=("${common_cxxflags[@]}" -I"${vhacd_source}/public" -I"${vhacd_source}/inc")
vhacd_units=(FloatMath VHACD-ASYNC VHACD btAlignedAllocator btConvexHullComputer vhacdICHull vhacdManifoldMesh vhacdMesh vhacdRaycastMesh vhacdVolume)
for name in "${vhacd_units[@]}"; do
  run_step "vhacd-compile-${name}" "${cxx[@]}" "${vhacd_flags[@]}" \
    -c "${vhacd_source}/src/${name}.cpp" -o "${vhacd_build}/${name}.o"
done
run_step vhacd-archive "${llvm_bin}/llvm-ar" rcs "${vhacd_build}/libVHACD_fPIC.a" \
  "${vhacd_build}"/*.o
run_step vhacd-pic-link "${cxx[@]}" -shared -Wl,-z,defs -Wl,--whole-archive \
  "${vhacd_build}/libVHACD_fPIC.a" -Wl,--no-whole-archive "${link_libs[@]}" \
  -o "${run_dir}/libvhacd_probe.so"

# --- Architecture verification ------------------------------------------
step=architecture
for archive in "${vorbis_build}/libvorbis.a" "${vorbis_build}/libvorbisenc.a" \
  "${vorbis_build}/libvorbisfile.a" "${vhacd_build}/libVHACD_fPIC.a"; do
  verify_archive "${archive}"
done

# --- Install Vorbis and VHACD ------------------------------------------
vorbis_destination="${vorbis_source}/lib/Unix/${triple}"
: > "${run_dir}/installed.sha256"
install_archive "${vorbis_build}/libvorbis.a" "${vorbis_destination}/libvorbis.a"
install_archive "${vorbis_build}/libvorbisenc.a" "${vorbis_destination}/libvorbisenc.a"
install_archive "${vorbis_build}/libvorbisfile.a" "${vorbis_destination}/libvorbisfile.a"
install_archive "${vhacd_build}/libVHACD_fPIC.a" "${vhacd_source}/Lib/Linux/${triple}/libVHACD_fPIC.a"

# --- libPNG -------------------------------------------------------------
png_source="${third_party}/libPNG/libPNG-1.5.27"
png_build="${build_dir}/libpng"
rm -rf "${png_build}"
mkdir -p "${png_build}"

png_units=(png pngerror pngget pngmem pngpread pngread pngrio pngrtran pngrutil pngset pngtrans pngwio pngwrite pngwtran pngwutil)
png_flags=("${common_cflags[@]}" -I"${png_source}" -I"${third_party}/zlib/1.3/include")
for name in "${png_units[@]}"; do
  run_step "png-compile-${name}" "${cc[@]}" "${png_flags[@]}" \
    -c "${png_source}/${name}.c" -o "${png_build}/${name}.o"
done
png_objs=()
for name in "${png_units[@]}"; do png_objs+=("${png_build}/${name}.o"); done
run_step png-archive "${llvm_bin}/llvm-ar" rcs "${png_build}/libpng.a" "${png_objs[@]}"

zlib_archive="${third_party}/zlib/1.3/lib/Unix/${triple}/Release/libz.a"
run_step png-pic-link "${cc[@]}" -shared -Wl,-z,defs -Wl,--whole-archive \
  "${png_build}/libpng.a" "${zlib_archive}" -Wl,--no-whole-archive -lm \
  -o "${run_dir}/libpng_probe.so"

install_archive "${png_build}/libpng.a" "${png_source}/lib/Unix/${triple}/libpng.a"

# --- KissFFT ------------------------------------------------------------
kissfft_source="${third_party}/Kiss_FFT/kiss_fft129"
kissfft_build="${build_dir}/kissfft"
rm -rf "${kissfft_build}"
mkdir -p "${kissfft_build}"

kissfft_flags=("${common_cflags[@]}" -I"${kissfft_source}")
kissfft_units=(kiss_fft tools/kfc tools/kiss_fftnd tools/kiss_fftndr tools/kiss_fftr)
kissfft_objs=()
for unit in "${kissfft_units[@]}"; do
  object="${kissfft_build}/$(basename "${unit}").o"
  run_step "kissfft-compile-${unit//\//-}" "${cc[@]}" "${kissfft_flags[@]}" \
    -c "${kissfft_source}/${unit}.c" -o "${object}"
  kissfft_objs+=("${object}")
done
run_step kissfft-archive "${llvm_bin}/llvm-ar" rcs "${kissfft_build}/libKissFFT_fPIC.a" \
  "${kissfft_objs[@]}"
run_step kissfft-pic-link "${cc[@]}" -shared -Wl,-z,defs -Wl,--whole-archive \
  "${kissfft_build}/libKissFFT_fPIC.a" -Wl,--no-whole-archive -lm \
  -o "${run_dir}/libKissFFT_probe.so"
verify_archive "${kissfft_build}/libKissFFT_fPIC.a"
install_archive "${kissfft_build}/libKissFFT_fPIC.a" \
  "${kissfft_source}/Lib/Linux/Release/${triple}/libKissFFT_fPIC.a"

# --- libxml2 -------------------------------------------------------------
xml_source_tar="${third_party}/libxml2/libxml2-2.9.10.tar.gz"
xml_source="${third_party}/libxml2/libxml2-2.9.10"
xml_build="${build_dir}/libxml2"
rm -rf "${xml_build}"
mkdir -p "${xml_build}"
mkdir -p "${xml_build}/source"
run_step libxml2-extract tar -xzf "${xml_source_tar}" -C "${xml_build}" \
  --strip-components=1
cd "${xml_build}"
run_step libxml2-configure env CC="${llvm_bin}/clang" CXX="${llvm_bin}/clang++" \
  CFLAGS="--target=${triple} --sysroot=${sysroot} -fPIC -O2" \
  CXXFLAGS="--target=${triple} --sysroot=${sysroot} -fPIC -O2" \
  LDFLAGS="--target=${triple} --sysroot=${sysroot} -fuse-ld=lld" \
  "${xml_build}/configure" --prefix="${xml_build}/install" \
  --with-pic=yes --with-python=no --with-lzma=no

zlib_archive="${third_party}/zlib/1.3/lib/Unix/${triple}/Release/libz.a"
xml_flags=("${common_cflags[@]}" -I"${xml_build}/include" -I"${third_party}/zlib/1.3/include")
xml_units=(SAX entities encoding error parserInternals parser tree hash list xmlIO xmlmemory uri valid xlink HTMLparser HTMLtree debugXML xpath xpointer xinclude nanohttp nanoftp catalog globals threads c14n xmlstring buf xmlregexp xmlschemas xmlschemastypes xmlunicode xmlreader relaxng dict SAX2 xmlwriter legacy chvalid pattern xmlsave xmlmodule schematron)
xml_objs=()
for unit in "${xml_units[@]}"; do
  object="${xml_build}/source/${unit}.o"
  run_step "libxml2-compile-${unit}" "${cc[@]}" "${xml_flags[@]}" \
    -DHAVE_CONFIG_H -c "${xml_build}/${unit}.c" -o "${object}"
  xml_objs+=("${object}")
done
run_step libxml2-archive "${llvm_bin}/llvm-ar" rcs "${xml_build}/libxml2.a" "${xml_objs[@]}"
run_step libxml2-pic-link "${cc[@]}" -shared -Wl,-z,defs -Wl,--whole-archive \
  "${xml_build}/libxml2.a" "${zlib_archive}" -Wl,--no-whole-archive -lm -ldl \
  -o "${run_dir}/libxml2_probe.so"
verify_archive "${xml_build}/libxml2.a"
install_archive "${xml_build}/libxml2.a" "${xml_source}/lib/${triple}/libxml2.a"

# --- FontConfig ----------------------------------------------------------
fontconfig_bootstrap="${build_dir}/fontconfig-bootstrap"
fontconfig_build="${build_dir}/fontconfig"
if [[ ! -f "${fontconfig_bootstrap}/Makefile.in" || ! -x "${fontconfig_bootstrap}/configure" ]]; then
  echo "FontConfig bootstrap cache is missing generated configure/Makefile.in" >&2
  echo "Expected: ${fontconfig_bootstrap}" >&2
  exit 2
fi
rm -rf "${fontconfig_build}"
mkdir -p "${fontconfig_build}/source"
run_step fontconfig-copy cp -a "${fontconfig_bootstrap}/." \
  "${fontconfig_build}/source/"
run_step fontconfig-aux install -m 0755 \
  "${build_dir}/libxml2/install-sh" \
  "${fontconfig_build}/source/install-sh"
run_step fontconfig-ltmain install -m 0644 \
  "${build_dir}/libxml2/ltmain.sh" \
  "${fontconfig_build}/source/ltmain.sh"
run_step fontconfig-config-scripts install -m 0755 \
  "${build_dir}/libxml2/config.guess" \
  "${build_dir}/libxml2/config.sub" \
  -t "${fontconfig_build}/source"

freetype_archive="${third_party}/FreeType2/FreeType2-2.10.0/lib/Unix/${triple}/libfreetype_fPIC.a"
png_archive="${third_party}/libPNG/libPNG-1.5.27/lib/Unix/${triple}/libpng.a"
xml_archive="${xml_source}/lib/${triple}/libxml2.a"
zlib_archive="${third_party}/zlib/1.3/lib/Unix/${triple}/Release/libz.a"
fontconfig_libs=("${freetype_archive}" "${png_archive}" "${xml_archive}" "${zlib_archive}" -ldl -lm)
fontconfig_cppflags=(-I"${third_party}/FreeType2/FreeType2-2.10.0/include" \
  -I"${xml_source}/include")

cd "${fontconfig_build}/source"
run_step fontconfig-configure env CC="${llvm_bin}/clang" CXX="${llvm_bin}/clang++" \
  CFLAGS="--target=${triple} --sysroot=${sysroot} -fPIC -O2" \
  CXXFLAGS="--target=${triple} --sysroot=${sysroot} -fPIC -O2" \
  LDFLAGS="--target=${triple} --sysroot=${sysroot} -fuse-ld=lld" \
  LIBS="${fontconfig_libs[*]}" \
  CPPFLAGS="${fontconfig_cppflags[*]}" \
  ./configure --prefix="${fontconfig_build}/install" \
  --enable-libxml2 --enable-static --disable-shared --with-pic=yes \
  --disable-docs

run_step fontconfig-generated-headers make -C "${fontconfig_build}/source/src" \
  fcalias.h fcftalias.h ../fc-case/fccase.h ../fc-lang/fclang.h stamp-fcstdint
run_step fontconfig-build make -C "${fontconfig_build}/source" -j"${jobs}" \
  -C src fcobjshash.gperf
run_step fontconfig-build make -C "${fontconfig_build}/source" -j"${jobs}" \
  -C src libfontconfig.la
fontconfig_library="${fontconfig_build}/source/src/.libs/libfontconfig.a"
fontconfig_clean_archive="${fontconfig_build}/libfontconfig-clean.a"
rm -f "${fontconfig_clean_archive}"
for member in $("${llvm_bin}/llvm-ar" t "${fontconfig_library}"); do
  [[ "${member}" == *.o ]] || continue
  run_step "fontconfig-archive-${member}" "${llvm_bin}/llvm-ar" \
    rcs "${fontconfig_clean_archive}" \
    "${fontconfig_build}/source/src/${member}"
done
[[ -s "${fontconfig_clean_archive}" ]] || {
  echo "FontConfig static archive contains no object members: ${fontconfig_library}" >&2
  exit 2
}
[[ -s "${fontconfig_library}" ]] || {
  echo "FontConfig static archive is missing: ${fontconfig_library}" >&2
  exit 2
}
run_step fontconfig-pic-link "${cc[@]}" -shared -Wl,-z,defs \
  -Wl,--whole-archive "${fontconfig_clean_archive}" -Wl,--no-whole-archive \
  "${fontconfig_libs[@]}" -o "${run_dir}/libfontconfig_probe.so"
verify_archive "${fontconfig_clean_archive}"
install_archive "${fontconfig_clean_archive}" \
  "${build_dir}/fontconfig/libfontconfig.a"
cd - >/dev/null

# --- metis ---------------------------------------------------------------
metis_root="${third_party}/metis"
metis_version="5.1.0"
metis_source_tar="${metis_root}/metis-${metis_version}.tar.xz"
metis_source="${metis_root}/${metis_version}"
metis_build="${build_dir}/metis"
rm -rf "${metis_build}"
mkdir -p "${metis_build}"
mkdir -p "${metis_build}/objects"
if [[ ! -f "${metis_source_tar}" ]]; then
  run_step metis-download curl -L --fail --silent --show-error \
    -o "${metis_source_tar}" \
    "https://deb.debian.org/debian/pool/main/m/metis/metis_5.1.0.dfsg.orig.tar.xz"
fi
run_step metis-extract tar -xJf "${metis_source_tar}" -C "${metis_build}" \
  --strip-components=1

metis_flags=("${common_cflags[@]}" -I"${metis_build}/include" -I"${metis_build}/GKlib" \
  -I"${metis_build}/libmetis" -D_GNU_SOURCE -DNDEBUG -DNDEBUG2)
metis_objs=()
for source in "${metis_build}"/GKlib/*.c; do
  unit="$(basename "${source}" .c)"
  object="${metis_build}/objects/GKlib-${unit}.o"
  run_step "metis-compile-gklib-${unit}" "${cc[@]}" "${metis_flags[@]}" \
    -c "${source}" -o "${object}"
  metis_objs+=("${object}")
done
for source in "${metis_build}"/libmetis/*.c; do
  unit="$(basename "${source}" .c)"
  object="${metis_build}/objects/libmetis-${unit}.o"
  run_step "metis-compile-libmetis-${unit}" "${cc[@]}" "${metis_flags[@]}" \
    -c "${source}" -o "${object}"
  metis_objs+=("${object}")
done
run_step metis-archive "${llvm_bin}/llvm-ar" rcs "${metis_build}/libmetis.a" "${metis_objs[@]}"
run_step metis-pic-link "${cc[@]}" -shared -Wl,-z,defs -Wl,--whole-archive \
  "${metis_build}/libmetis.a" -Wl,--no-whole-archive -lm \
  -o "${run_dir}/libmetis_probe.so"
verify_archive "${metis_build}/libmetis.a"
install_archive "${metis_build}/libmetis.a" \
  "${metis_source}/libmetis/Linux/${triple}/Release/libmetis.a"

# --- FreeImage -----------------------------------------------------------
freeimage_source="${third_party}/FreeImage/FreeImage-3.18.0"
freeimage_build="${build_dir}/freeimage"
freeimage_destination="${ue_dir}/Engine/Binaries/ThirdParty/FreeImage/Linux/libfreeimage-3.18.0.so"
rm -rf "${freeimage_build}"
mkdir -p "${freeimage_build}"
run_step freeimage-clean make -C "${freeimage_source}" -f Makefile.gnu clean
run_step freeimage-build env CC="${llvm_bin}/clang" CXX="${llvm_bin}/clang++" \
  AR="${llvm_bin}/llvm-ar" \
  CFLAGS="--target=${triple} --sysroot=${sysroot} -fuse-ld=lld -std=gnu89 ${common_cflags[*]}" \
  CXXFLAGS="--target=${triple} --sysroot=${sysroot} -fuse-ld=lld -std=c++11 -Wno-deprecated-declarations -Wno-dynamic-exception-spec ${common_cxxflags[*]}" \
  LDFLAGS="--target=${triple} --sysroot=${sysroot} -fuse-ld=lld" \
  make -C "${freeimage_source}" -f Makefile.gnu -j"${jobs}" \
  libfreeimage-3.18.0.so

freeimage_library="${freeimage_source}/libfreeimage-3.18.0.so"
run_step freeimage-install install -D -m 0755 "${freeimage_library}" \
  "${freeimage_destination}"
run_step freeimage-install-header install -D -m 0644 \
  "${freeimage_source}/Source/FreeImage.h" \
  "${freeimage_source}/Dist/FreeImage.h"
step=freeimage-architecture
"${llvm_bin}/llvm-readelf" -h "${freeimage_destination}" > "${run_dir}/libfreeimage-3.18.0.so.elf.txt"
awk '/Machine:/{found=1; if ($2 != "AArch64") bad=1} END{exit !found || bad}' \
  "${run_dir}/libfreeimage-3.18.0.so.elf.txt"
printf '%s  %s\n' "$(sha256sum "${freeimage_destination}" | awk '{print $1}')" \
  "${freeimage_destination}" >> "${run_dir}/installed.sha256"
step=complete
