#!/usr/bin/env bash
# Fetch the pinned Khronos Vulkan validation layer used by the opt-in GB10
# runtime diagnostics.
#
# The layer is deliberately not baked into the toolchain image. The image is
# Ubuntu 22.04 (glibc 2.35), so a jammy-built layer is required, and the archive
# is fetched from the host where the Ubuntu ports mirror is reachable. The
# bundle is extracted into one flat directory: the layer manifest ships a
# relative library_path, which is exactly the layout VK_LAYER_PATH expects, and
# libVkLayer_utils.so is its only non-system dependency.
set -Eeuo pipefail
umask 022

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

# Pinned to the jammy archive build that matches the toolchain image's
# libvulkan1 1.3.204: api_version 1.3.204 and a maximum GLIBC_2.32 requirement.
layer_version='1.3.204.1-2'
deb_name="vulkan-validationlayers_${layer_version}_arm64.deb"
deb_url="http://ports.ubuntu.com/ubuntu-ports/pool/universe/v/vulkan-validationlayers/${deb_name}"
deb_sha256='dbc3a59a0191e4b4973b9b1405b9ccc4754d51c1c7ba69f9977d7e94fa5a316f'
layer_name='VK_LAYER_KHRONOS_validation'
manifest_name='VkLayer_khronos_validation.json'

target_dir="${CARLA_VALIDATION_LAYER_DIR:-${repo_root}/data/cache/vulkan-validation-layer}"

for tool in curl sha256sum dpkg-deb python3; do
  command -v "${tool}" >/dev/null || { echo "${tool} is required to fetch the layer" >&2; exit 2; }
done

work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

echo "fetching ${deb_url}"
curl -fsSL --retry 3 --max-time 300 -o "${work_dir}/${deb_name}" "${deb_url}"
printf '%s  %s\n' "${deb_sha256}" "${work_dir}/${deb_name}" | sha256sum --check --status - || {
  echo "validation layer archive checksum mismatch" >&2; exit 3;
}

dpkg-deb -x "${work_dir}/${deb_name}" "${work_dir}/extracted"
lib_dir="${work_dir}/extracted/usr/lib/aarch64-linux-gnu"
manifest="${work_dir}/extracted/usr/share/vulkan/explicit_layer.d/${manifest_name}"
for path in "${lib_dir}/libVkLayer_khronos_validation.so" \
  "${lib_dir}/libVkLayer_utils.so" "${manifest}"; do
  [[ -f "${path}" ]] || { echo "validation layer archive is missing ${path}" >&2; exit 3; }
done

# The manifest must resolve its library from its own directory; a path-qualified
# library_path would make VK_LAYER_PATH unusable without touching system paths.
python3 - "${manifest}" "${layer_name}" <<'PY'
import json
import sys

manifest, expected = sys.argv[1], sys.argv[2]
with open(manifest, encoding="utf-8") as stream:
    layer = json.load(stream)["layer"]
if layer["name"] != expected:
    raise SystemExit(f"unexpected layer name: {layer['name']}")
if "/" in layer["library_path"]:
    raise SystemExit(f"layer library_path is not relative: {layer['library_path']}")
PY

if [[ -e "${target_dir}" && ! -w "${target_dir}" ]]; then
  echo "${target_dir} is not writable; remove it first" >&2
  echo "a directory created by docker before the first fetch needs sudo rm -rf ${target_dir}" >&2
  exit 3
fi
rm -rf "${target_dir}"
mkdir -p "${target_dir}"
install -m 0644 "${lib_dir}/libVkLayer_khronos_validation.so" "${target_dir}/"
install -m 0644 "${lib_dir}/libVkLayer_utils.so" "${target_dir}/"
install -m 0644 "${manifest}" "${target_dir}/"

python3 - "${target_dir}" "${deb_name}" "${deb_url}" "${deb_sha256}" <<'PY'
import hashlib
import json
import pathlib
import sys

target, deb_name, deb_url, deb_sha256 = sys.argv[1:5]
root = pathlib.Path(target)
with open(root / "VkLayer_khronos_validation.json", encoding="utf-8") as stream:
    layer = json.load(stream)["layer"]
files = {
    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(root.iterdir())
    if path.is_file()
}
provenance = {
    "schema": 1,
    "source": {"archive": deb_name, "url": deb_url, "sha256": deb_sha256},
    "layer": {
        "name": layer["name"],
        "api_version": layer["api_version"],
        "library_path": layer["library_path"],
    },
    "files": files,
}
with open(root / "provenance.json", "w", encoding="utf-8") as stream:
    json.dump(provenance, stream, indent=2, sort_keys=True)
    stream.write("\n")
print(f"api_version={layer['api_version']} files={len(files)}")
PY

echo "PASS vulkan-validation-layer dir=${target_dir}"
