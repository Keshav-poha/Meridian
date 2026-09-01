"""Typed edge input contract; feature ordering is resolved from shared JSON."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class TelemetryFrame:
    """One IMU observation for replay or a high-rate external IMU stream."""

    timestamp_s: float
    acceleration_mps2: Sequence[float]
    gyroscope_rps: Sequence[float]
    magnetometer_ut: Sequence[float]
    latitude_deg: float | None = None
    longitude_deg: float | None = None

    def __post_init__(self) -> None:
        for name, values in (
            ("acceleration_mps2", self.acceleration_mps2),
            ("gyroscope_rps", self.gyroscope_rps),
            ("magnetometer_ut", self.magnetometer_ut),
        ):
            if len(values) != 3:
                raise ValueError(f"{name} must have exactly three axes")
