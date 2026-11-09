#include "vehicle_state.hpp"

#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace autodrive::job_track {
namespace {

constexpr double kMetersPerSecondToKph = 3.6;
constexpr double kKphToMetersPerSecond = 1.0 / kMetersPerSecondToKph;

}  // namespace

bool VehicleState::is_finite() const noexcept {
  return std::isfinite(x_m) && std::isfinite(y_m) &&
         std::isfinite(yaw_rad) && std::isfinite(speed_mps);
}

double VehicleState::speed_kph() const noexcept {
  return speed_mps * kMetersPerSecondToKph;
}

void VehicleState::validate() const {
  if (!is_finite()) {
    throw std::invalid_argument("VehicleState contains NaN or infinity");
  }
  if (speed_mps < 0.0) {
    throw std::invalid_argument("VehicleState speed_mps must be non-negative");
  }
}

VehicleState from_speed_kph(
    double x_m, double y_m, double yaw_rad, double speed_kph) {
  VehicleState state{x_m, y_m, yaw_rad, speed_kph * kKphToMetersPerSecond};
  state.validate();
  return state;
}

std::string describe(const VehicleState& state) {
  state.validate();
  std::ostringstream output;
  output << std::fixed << std::setprecision(3)
         << "x=" << state.x_m << " m, "
         << "y=" << state.y_m << " m, "
         << "yaw=" << state.yaw_rad << " rad, "
         << "speed=" << state.speed_mps << " m/s";
  return output.str();
}

}  // namespace autodrive::job_track
