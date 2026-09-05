"""Typed edge input contract; feature ordering is resolved from shared JSON."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence


_MIN_MAGNETIC_DIRECTION_NORM = 0.5
_MAX_MAGNETIC_DIRECTION_NORM = 1.5


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
        """Validate and freeze a single calibrated edge observation.

        Freezing the axis sequences matters here: a caller can otherwise pass a
        mutable list, mutate it after validation, and make a later window
        contain values that were never checked.
        """

        timestamp_s = _finite_scalar("timestamp_s", self.timestamp_s)
        object.__setattr__(self, "timestamp_s", timestamp_s)

        for name, values in (
            ("linear_acceleration_vehicle_mps2", self.linear_acceleration_vehicle_mps2),
            ("gyroscope_vehicle_rps", self.gyroscope_vehicle_rps),
            ("magnetic_direction_vehicle", self.magnetic_direction_vehicle),
        ):
            object.__setattr__(self, name, _three_axis_vector(name, values))

        magnetic_norm = math.sqrt(sum(axis * axis for axis in self.magnetic_direction_vehicle))
        if not _MIN_MAGNETIC_DIRECTION_NORM <= magnetic_norm <= _MAX_MAGNETIC_DIRECTION_NORM:
            raise ValueError(
                "magnetic_direction_vehicle must be a normalized direction "
                f"(norm in [{_MIN_MAGNETIC_DIRECTION_NORM}, {_MAX_MAGNETIC_DIRECTION_NORM}]); "
                f"received {magnetic_norm:.3f}"
            )

        if (self.latitude_deg is None) != (self.longitude_deg is None):
            raise ValueError("latitude_deg and longitude_deg must be supplied together")
        if self.latitude_deg is not None and self.longitude_deg is not None:
            latitude_deg = _finite_scalar("latitude_deg", self.latitude_deg)
            longitude_deg = _finite_scalar("longitude_deg", self.longitude_deg)
            if not -90.0 <= latitude_deg <= 90.0:
                raise ValueError("latitude_deg must be between -90 and 90")
            if not -180.0 <= longitude_deg <= 180.0:
                raise ValueError("longitude_deg must be between -180 and 180")
            object.__setattr__(self, "latitude_deg", latitude_deg)
            object.__setattr__(self, "longitude_deg", longitude_deg)


def _finite_scalar(name: str, value: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _three_axis_vector(name: str, values: Sequence[float]) -> tuple[float, float, float]:
    try:
        result = tuple(float(value) for value in values)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an iterable of three finite axes") from error
    if len(result) != 3:
        raise ValueError(f"{name} must have exactly three axes")
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{name} axes must all be finite")
    return (result[0], result[1], result[2])
