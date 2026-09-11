#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${repo_root}"

env_file="${ENV_FILE:-.env}"
# shellcheck source=../lib/load-env.sh
source "${repo_root}/scripts/lib/load-env.sh"
load_env_file "${env_file}" || {
  echo "unable to load env file: ${env_file}" >&2
  exit 2
}

scenario_file="${SCENARIO_FILE_HOST:-config/scenario/sample-scenario.yaml}"
map_dir="${MAPS_DIR:-./data/maps}"
sample_url="${SCENARIO_SAMPLE_URL:-https://raw.githubusercontent.com/autowarefoundation/autoware_sample_scenarios/main/sample-scenario.yaml}"

[[ -s "${map_dir}/lanelet2_map.osm" ]] || {
  echo "lanelet map not found: ${map_dir}/lanelet2_map.osm" >&2
  exit 66
}
[[ -s "${map_dir}/pointcloud_map.pcd" ]] || {
  echo "pointcloud map not found: ${map_dir}/pointcloud_map.pcd" >&2
  exit 66
}

mkdir -p "$(dirname "${scenario_file}")" data/reports/scenario

if [[ ! -s "${scenario_file}" ]]; then
  command -v curl >/dev/null 2>&1 || {
    echo "curl is required to download the sample scenario" >&2
    exit 69
  }
  temp_download="$(mktemp "${scenario_file}.download.XXXXXX")"
  trap 'rm -f "${temp_download}"' EXIT
  curl -fsSL "${sample_url}" >"${temp_download}"
  mv "${temp_download}" "${scenario_file}"
  trap - EXIT
fi

# Scenario files use container-visible absolute paths, while MAPS_DIR may be a
# different host directory such as ./data/maps/sample-map-planning.
temp_patched="$(mktemp "${scenario_file}.patched.XXXXXX")"
trap 'rm -f "${temp_patched}"' EXIT
sed -E \
  -e 's#(filepath:[[:space:]]*).*/lanelet2_map[.]osm#\1/data/maps/lanelet2_map.osm#' \
  -e 's#(filepath:[[:space:]]*).*/pointcloud_map[.]pcd#\1/data/maps/pointcloud_map.pcd#' \
  "${scenario_file}" >"${temp_patched}"
mv "${temp_patched}" "${scenario_file}"
trap - EXIT

echo "scenario file: ${scenario_file}"
echo "map directory: ${map_dir}"
grep -A5 'RoadNetwork:' "${scenario_file}"
