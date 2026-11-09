#pragma once

#include <string>

namespace autodrive::job_track {

struct VehicleState {
  double x_m{0.0};
  double y_m{0.0};
  double yaw_rad{0.0};
  double speed_mps{0.0};

  [[nodiscard]] bool is_finite() const noexcept;
  [[nodiscard]] double speed_kph() const noexcept;
  void validate() const;
};

[[nodiscard]] VehicleState from_speed_kph(
    double x_m, double y_m, double yaw_rad, double speed_kph);

[[nodiscard]] std::string describe(const VehicleState& state);

}  // namespace autodrive::job_track
