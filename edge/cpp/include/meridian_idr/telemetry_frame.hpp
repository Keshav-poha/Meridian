#pragma once

#include <array>
#include <optional>

namespace meridian_idr {

struct TelemetryFrame {
  double timestamp_s{};
  // Calibrated body-to-vehicle axes; gravity removed from acceleration.
  std::array<float, 3> linear_acceleration_vehicle_mps2{};
  std::array<float, 3> gyroscope_vehicle_rps{};
  std::array<float, 3> magnetic_direction_vehicle{};
  std::optional<double> latitude_deg{};
  std::optional<double> longitude_deg{};
};

}  // namespace meridian_idr
