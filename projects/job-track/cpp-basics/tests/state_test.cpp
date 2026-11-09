#include <cassert>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>

#include "vehicle_state.hpp"

using autodrive::job_track::VehicleState;
using autodrive::job_track::describe;
using autodrive::job_track::from_speed_kph;

int main() {
  const VehicleState default_state{};
  assert(default_state.is_finite());
  assert(default_state.speed_mps == 0.0);
  assert(default_state.speed_kph() == 0.0);

  const VehicleState converted = from_speed_kph(1.0, 2.0, 0.5, 36.0);
  assert(std::abs(converted.speed_mps - 10.0) < 1e-12);
  assert(std::abs(converted.speed_kph() - 36.0) < 1e-12);
  assert(describe(converted).find("speed=10.000 m/s") != std::string::npos);

  bool rejected_negative_speed = false;
  try {
    VehicleState invalid{0.0, 0.0, 0.0, -1.0};
    invalid.validate();
  } catch (const std::invalid_argument&) {
    rejected_negative_speed = true;
  }
  assert(rejected_negative_speed);

  bool rejected_nonfinite = false;
  try {
    VehicleState invalid{
        std::numeric_limits<double>::quiet_NaN(), 0.0, 0.0, 0.0};
    invalid.validate();
  } catch (const std::invalid_argument&) {
    rejected_nonfinite = true;
  }
  assert(rejected_nonfinite);

  return 0;
}
