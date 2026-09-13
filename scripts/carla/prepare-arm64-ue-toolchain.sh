#!/usr/bin/env bash
set -Eeuo pipefail

ue_dir="${1:-/workspace/unreal-engine}"
llvm_bin="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
sdk_root="${ue_dir}/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64"
sysroot="$(find "${sdk_root}" -type d -path '*aarch64-unknown-linux-gnueabi' -print -quit 2>/dev/null || true)"

[[ -d "${sysroot}" ]] || {
  echo "ARM64 UE sysroot not found under ${sdk_root}" >&2
  exit 2
}
[[ -x "${llvm_bin}/clang++" && -x "${llvm_bin}/llvm-ar" ]] || {
  echo "native LLVM 18 toolchain is missing under ${llvm_bin}" >&2
  exit 2
}

bin_dir="${sysroot}/bin"
backup_dir="${bin_dir}/carla-x86-backup"
mkdir -p "${backup_dir}"

for tool in clang clang++; do
  if [[ -f "${bin_dir}/${tool}" && ! -L "${bin_dir}/${tool}" ]]; then
    mv -f "${bin_dir}/${tool}" "${backup_dir}/${tool}"
  fi
  cat > "${bin_dir}/${tool}" <<EOF
#!/usr/bin/env bash
# CARLA_ARM64_NATIVE_WRAPPER=1
exec "${llvm_bin}/${tool}" \\
  --target=aarch64-unknown-linux-gnueabi \\
  --sysroot="${sysroot}" \\
  "\$@"
EOF
  chmod 0755 "${bin_dir}/${tool}"
done

for tool in llvm-ar llvm-nm llvm-objcopy llvm-objdump llvm-ranlib llvm-readelf llvm-strip lld ld.lld; do
  native="${llvm_bin}/${tool}"
  [[ -x "${native}" ]] || continue
  if [[ -e "${bin_dir}/${tool}" && ! -L "${bin_dir}/${tool}" ]]; then
    mv -f "${bin_dir}/${tool}" "${backup_dir}/${tool}"
  fi
  ln -sfn "${native}" "${bin_dir}/${tool}"
done

printf 'native_llvm_bin=%s\n' "${llvm_bin}"
printf 'arm64_sysroot=%s\n' "${sysroot}"
file "${bin_dir}/clang"
"${bin_dir}/clang" --version
printf 'prepared=1\n'
