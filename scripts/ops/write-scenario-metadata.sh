#!/usr/bin/env bash
set -Eeuo pipefail

scenario_id="${SCENARIO_ID:-manual}"
map_version="${MAP_VERSION:-unset}"
vehicle_model="${VEHICLE_MODEL:-unset}"
sensor_model="${SENSOR_MODEL:-unset}"
ros_distro="${ROS_DISTRO:-unset}"
ros_domain_id="${ROS_DOMAIN_ID:-unset}"
random_seed="${RANDOM_SEED:-unset}"

for value in \
  "${scenario_id}" \
  "${map_version}" \
  "${vehicle_model}" \
  "${sensor_model}" \
  "${ros_distro}" \
  "${ros_domain_id}" \
  "${random_seed}"; do
  [[ "${value}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
    echo "scenario metadata values may only contain letters, numbers, dot, underscore, and hyphen" >&2
    exit 64
  }
done

output_dir="${GROUND_TRUTH_OUTPUT_DIR:-/data/ground_truth}"
mkdir -p "${output_dir}"
output="${output_dir}/scenario-${scenario_id}.json"
temp_output="$(mktemp "${output}.tmp.XXXXXX")"
trap 'rm -f "${temp_output}"' EXIT

cat > "${temp_output}" <<EOF
{
  "scenario_id": "${scenario_id}",
  "map_version": "${map_version}",
  "vehicle_model": "${vehicle_model}",
  "sensor_model": "${sensor_model}",
  "ros_distro": "${ros_distro}",
  "ros_domain_id": "${ros_domain_id}",
  "random_seed": "${random_seed}",
  "created_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

mv "${temp_output}" "${output}"
trap - EXIT
echo "scenario metadata written to ${output}"
