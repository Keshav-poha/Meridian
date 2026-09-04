"""Classical strapdown dead reckoning with vehicle non-holonomic constraints."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

from .calibration import CalibrationResult, apply_mount_rotation, wrap_angle


EARTH_RADIUS_M = 6_378_137.0


def latlon_to_enu(latitude_deg: np.ndarray, longitude_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Equirectangular local ENU projection, appropriate for replay segments."""
    latitude = np.deg2rad(np.asarray(latitude_deg, dtype=float))
    longitude = np.deg2rad(np.asarray(longitude_deg, dtype=float))
    return (
        (longitude - longitude[0]) * EARTH_RADIUS_M * math.cos(float(latitude[0])),
        (latitude - latitude[0]) * EARTH_RADIUS_M,
    )


def _moving_average(values: np.ndarray, samples: int) -> np.ndarray:
    if samples <= 1:
        return values.copy()
    kernel = np.ones(samples, dtype=float) / samples
    return np.convolve(values, kernel, mode="same")


@dataclass(frozen=True)
class DeadReckoningResult:
    timestamp_s: list[float]
    east_m: list[float]
    north_m: list[float]
    speed_mps: list[float]
    heading_rad: list[float]
    gyro_z_bias_rps: float

    def as_frame(self) -> pd.DataFrame:
        return pd.DataFrame(asdict(self)).rename(columns={"timestamp_s": "timestamp_s"})


@dataclass(frozen=True)
class DriftMetrics:
    distance_travelled_m: float
    end_position_error_m: float
    trajectory_rmse_m: float
    drift_percent: float
    update_rate_hz: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def classical_nhc_dead_reckoning(
    frame: pd.DataFrame,
    calibration: CalibrationResult,
    *,
    shock_clip_mps2: float = 8.0,
    smoothing_seconds: float = 0.3,
    idle_speed_mps: float = 0.5,
) -> DeadReckoningResult:
    """Replay a GNSS-denied segment from its first GNSS-aided state.

    The NHC is explicit: the only integrated vehicle-frame velocity is forward
    speed. Lateral and vertical velocity are reset to zero every update, so a
    pothole cannot be interpreted as vehicle side-slip or flight.
    """
    required = {
        "timestamp_s", "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
        "gravity_x_mps2", "gravity_y_mps2", "gravity_z_mps2",
        "gyro_x_rps", "gyro_y_rps", "gyro_z_rps", "gt_speed_mps", "gt_heading_rad",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"dead reckoning frame missing: {sorted(missing)}")
    if len(frame) < 3:
        raise ValueError("dead reckoning needs at least three samples")
    timestamp = frame.timestamp_s.to_numpy(dtype=float)
    dt = np.diff(timestamp, prepend=timestamp[0])
    positive_dt = dt[dt > 0]
    if positive_dt.size == 0:
        raise ValueError("timestamps must increase")
    nominal_dt = float(np.median(positive_dt))
    dt[0] = nominal_dt
    if np.any(dt <= 0) or np.any(dt > 1.0):
        raise ValueError("timestamps contain invalid integration intervals")

    raw_accel = frame[["accel_x_mps2", "accel_y_mps2", "accel_z_mps2"]].to_numpy(dtype=float)
    gravity = frame[["gravity_x_mps2", "gravity_y_mps2", "gravity_z_mps2"]].to_numpy(dtype=float)
    raw_gyro = frame[["gyro_x_rps", "gyro_y_rps", "gyro_z_rps"]].to_numpy(dtype=float)
    vehicle_accel = apply_mount_rotation(raw_accel - gravity, calibration)
    vehicle_gyro = apply_mount_rotation(raw_gyro, calibration)
    smoothing_samples = max(1, round(smoothing_seconds / nominal_dt))
    forward_accel = np.clip(_moving_average(vehicle_accel[:, 0], smoothing_samples), -shock_clip_mps2, shock_clip_mps2)

    idle = frame.gt_speed_mps.to_numpy(dtype=float) <= idle_speed_mps
    gyro_bias = float(np.median(vehicle_gyro[idle, 2])) if int(idle.sum()) >= 5 else 0.0
    yaw_rate = vehicle_gyro[:, 2] - gyro_bias
    speed = np.empty(len(frame), dtype=float)
    heading = np.empty(len(frame), dtype=float)
    east = np.zeros(len(frame), dtype=float)
    north = np.zeros(len(frame), dtype=float)
    # The first speed/heading are available at loss time. After this point, no
    # reference position, heading, or speed is read by the integrator.
    speed[0] = max(0.0, float(frame.gt_speed_mps.iloc[0]))
    heading[0] = float(frame.gt_heading_rad.iloc[0])
    for index in range(1, len(frame)):
        heading[index] = float(wrap_angle(heading[index - 1] + yaw_rate[index] * dt[index]))
        speed[index] = max(0.0, speed[index - 1] + forward_accel[index] * dt[index])
        # NHC velocity vehicle-frame = [forward speed, 0, 0]. Heading is
        # clockwise from north, hence east=sin(heading), north=cos(heading).
        distance = 0.5 * (speed[index - 1] + speed[index]) * dt[index]
        east[index] = east[index - 1] + distance * math.sin(heading[index])
        north[index] = north[index - 1] + distance * math.cos(heading[index])
    return DeadReckoningResult(
        timestamp_s=timestamp.tolist(), east_m=east.tolist(), north_m=north.tolist(),
        speed_mps=speed.tolist(), heading_rad=heading.tolist(), gyro_z_bias_rps=gyro_bias,
    )


def learned_velocity_nhc_dead_reckoning(
    frame: pd.DataFrame,
    calibration: CalibrationResult,
    model: Any,
    normalization: dict[str, np.ndarray],
) -> DeadReckoningResult:
    """NHC replay using Stage 5 CNN speed while retaining classical heading.

    The first model window (2 seconds at the declared model rate) starts from
    the last GNSS-aided speed. Thereafter no GNSS/ground-truth speed is read.
    """
    from .velocity_model import predict_velocity_cnn

    required = {
        "timestamp_s", "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
        "gyro_x_rps", "gyro_y_rps", "gyro_z_rps", "gt_speed_mps", "gt_heading_rad",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"learned DR frame missing: {sorted(missing)}")
    timestamp = frame.timestamp_s.to_numpy(dtype=float)
    dt = np.diff(timestamp, prepend=timestamp[0])
    nominal_dt = float(np.median(dt[dt > 0]))
    dt[0] = nominal_dt
    rate = float(np.asarray(normalization["sample_rate_hz"]))
    window_samples = round(2.0 * rate)
    if len(frame) < window_samples:
        raise ValueError("blackout is shorter than one learned-velocity window")
    channels = [
        "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
        "gyro_x_rps", "gyro_y_rps", "gyro_z_rps", "mag_x_ut", "mag_y_ut", "mag_z_ut",
    ]
    raw_windows = np.stack(
        [frame.loc[index - window_samples + 1 : index, channels].to_numpy(np.float32) for index in range(window_samples - 1, len(frame))]
    )
    predicted_tail = np.maximum(0.0, predict_velocity_cnn(model, raw_windows, normalization))
    speed = np.empty(len(frame), dtype=float)
    speed[: window_samples - 1] = max(0.0, float(frame.gt_speed_mps.iloc[0]))
    speed[window_samples - 1 :] = predicted_tail

    raw_gyro = frame[["gyro_x_rps", "gyro_y_rps", "gyro_z_rps"]].to_numpy(dtype=float)
    vehicle_gyro = apply_mount_rotation(raw_gyro, calibration)
    idle = frame.gt_speed_mps.to_numpy(dtype=float) <= 0.5
    gyro_bias = float(np.median(vehicle_gyro[idle, 2])) if int(idle.sum()) >= 5 else 0.0
    heading = np.empty(len(frame), dtype=float)
    east, north = np.zeros(len(frame), dtype=float), np.zeros(len(frame), dtype=float)
    heading[0] = float(frame.gt_heading_rad.iloc[0])
    for index in range(1, len(frame)):
        heading[index] = float(wrap_angle(heading[index - 1] + (vehicle_gyro[index, 2] - gyro_bias) * dt[index]))
        distance = 0.5 * (speed[index - 1] + speed[index]) * dt[index]
        east[index] = east[index - 1] + distance * math.sin(heading[index])
        north[index] = north[index - 1] + distance * math.cos(heading[index])
    return DeadReckoningResult(
        timestamp_s=timestamp.tolist(), east_m=east.tolist(), north_m=north.tolist(),
        speed_mps=speed.tolist(), heading_rad=heading.tolist(), gyro_z_bias_rps=gyro_bias,
    )


def measure_drift(frame: pd.DataFrame, result: DeadReckoningResult) -> DriftMetrics:
    """Measure classical DR against held-out ground truth for one blackout."""
    expected = {"gt_latitude_deg", "gt_longitude_deg", "timestamp_s"}
    missing = expected.difference(frame.columns)
    if missing:
        raise ValueError(f"cannot measure drift without: {sorted(missing)}")
    gt_east, gt_north = latlon_to_enu(frame.gt_latitude_deg.to_numpy(), frame.gt_longitude_deg.to_numpy())
    estimate_east, estimate_north = np.asarray(result.east_m), np.asarray(result.north_m)
    errors = np.hypot(estimate_east - gt_east, estimate_north - gt_north)
    distance = float(np.hypot(np.diff(gt_east), np.diff(gt_north)).sum())
    dt = np.diff(frame.timestamp_s.to_numpy(dtype=float))
    update_rate = float(1.0 / np.median(dt[dt > 0]))
    end_error = float(errors[-1])
    return DriftMetrics(
        distance_travelled_m=distance,
        end_position_error_m=end_error,
        trajectory_rmse_m=float(np.sqrt(np.mean(np.square(errors)))),
        drift_percent=(100.0 * end_error / distance) if distance > 1e-6 else math.inf,
        update_rate_hz=update_rate,
    )


def select_blackout(frame: pd.DataFrame, *, start_seconds: float, duration_seconds: float) -> pd.DataFrame:
    """Return a bounded simulated GNSS outage from a synchronized replay."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    timestamp = frame.timestamp_s.to_numpy(dtype=float)
    mask = (timestamp >= start_seconds) & (timestamp <= start_seconds + duration_seconds)
    segment = frame.loc[mask].reset_index(drop=True)
    if len(segment) < 3:
        raise ValueError("requested blackout has fewer than three samples")
    return segment
