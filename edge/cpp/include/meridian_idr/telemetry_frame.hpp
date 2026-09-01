#pragma once

#include <array>
#include <optional>

namespace meridian_idr {

struct TelemetryFrame {
  double timestamp_s{};
  std::array<float, 3> acceleration_mps2{};
  std::array<float, 3> gyroscope_rps{};
  std::array<float, 3> magnetometer_ut{};
  std::optional<double> latitude_deg{};
  std::optional<double> longitude_deg{};
};

}  // namespace meridian_idr
