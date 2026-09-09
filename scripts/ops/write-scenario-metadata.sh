#!/usr/bin/env bash
set -Eeuo pipefail

output_dir="${GROUND_TRUTH_OUTPUT_DIR:-/data/ground_truth}"
mkdir -p "${output_dir}"
output="${output_dir}/scenario-${SCENARIO_ID:-manual}.json"

cat > "${output}" <<EOF
{
  "scenario_id": "${SCENARIO_ID:-manual}",
  "map_version": "${MAP_VERSION:-unset}",
  "vehicle_model": "${VEHICLE_MODEL:-unset}",
  "sensor_model": "${SENSOR_MODEL:-unset}",
  "ros_distro": "${ROS_DISTRO:-unset}",
  "ros_domain_id": "${ROS_DOMAIN_ID:-unset}",
  "random_seed": "${RANDOM_SEED:-unset}",
  "created_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "scenario metadata written to ${output}"
