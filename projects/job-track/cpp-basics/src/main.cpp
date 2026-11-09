#include <iostream>

#include "vehicle_state.hpp"

int main() {
  const auto state =
      autodrive::job_track::from_speed_kph(10.0, 2.0, 0.0, 36.0);
  std::cout << autodrive::job_track::describe(state) << '\n';
  return 0;
}
