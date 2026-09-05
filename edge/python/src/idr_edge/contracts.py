"""Typed edge input contract; feature ordering is resolved from shared JSON."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class TelemetryFrame:
    """One calibrated vehicle-frame IMU observation.

    The caller removes gravity and applies its body-to-vehicle mount transform
    before constructing this portable frame. This preserves the exact model
    feature contract for both FOG-grade edge IMUs and the mobile runtime.
    """

    timestamp_s: float
    linear_acceleration_vehicle_mps2: Sequence[float]
    gyroscope_vehicle_rps: Sequence[float]
    magnetic_direction_vehicle: Sequence[float]
    latitude_deg: float | None = None
    longitude_deg: float | None = None

    def __post_init__(self) -> None:
        for name, values in (
            ("linear_acceleration_vehicle_mps2", self.linear_acceleration_vehicle_mps2),
            ("gyroscope_vehicle_rps", self.gyroscope_vehicle_rps),
            ("magnetic_direction_vehicle", self.magnetic_direction_vehicle),
        ):
            if len(values) != 3:
                raise ValueError(f"{name} must have exactly three axes")
