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
class RuntimeGnssAnchoredReplay:
    """Offline output of the speed/state logic used by the mobile runtime.

    ``model_speed_mps`` is the bounded CNN prior for diagnostics. The position
    integration uses ``dead_reckoning.speed_mps`` instead: it starts from the
    GNSS-aided speed at loss and accepts only a rate-limited CNN *change* in
    speed, matching the deployed safety contract.
    """

    dead_reckoning: DeadReckoningResult
    model_speed_mps: list[float]
    forward_acceleration_mps2: list[float]


@dataclass(frozen=True)
class NavigationInitialState:
    """State available at the instant GNSS aiding is masked or lost."""

    speed_mps: float
    heading_rad: float
    gyro_z_bias_rps: float


def initial_state_from_preoutage_reference(
    history: pd.DataFrame,
    calibration: CalibrationResult,
) -> NavigationInitialState:
    """Freeze initial state and gyro bias from GNSS-aided *pre-loss* history.

    IO-VNBD names the vehicle reference fields ``gt_*``. They are a proxy for
    GNSS speed/course while aiding is available in this offline replay, but no
    such field is allowed into the blackout integrator itself.
    """
    required = {"gt_speed_mps", "gt_heading_rad", "gyro_x_rps", "gyro_y_rps", "gyro_z_rps"}
    missing = required.difference(history.columns)
    if missing:
        raise ValueError(f"pre-outage reference missing: {sorted(missing)}")
    valid_state = np.isfinite(history.gt_speed_mps.to_numpy(float)) & np.isfinite(
        history.gt_heading_rad.to_numpy(float)
    )
    if not valid_state.any():
        raise ValueError("pre-outage reference has no finite speed/heading state")
    final_index = int(np.flatnonzero(valid_state)[-1])
    gyro = apply_mount_rotation(
        history[["gyro_x_rps", "gyro_y_rps", "gyro_z_rps"]].to_numpy(float), calibration
    )[:, 2]
    idle = valid_state & (history.gt_speed_mps.to_numpy(float) <= 0.5) & np.isfinite(gyro)
    gyro_bias = float(np.median(gyro[idle])) if int(idle.sum()) >= 5 else 0.0
    return NavigationInitialState(
        speed_mps=max(0.0, float(history.gt_speed_mps.iloc[final_index])),
        heading_rad=float(history.gt_heading_rad.iloc[final_index]),
        gyro_z_bias_rps=gyro_bias,
    )


def _legacy_initial_state(frame: pd.DataFrame) -> NavigationInitialState:
    """Compatibility fallback for legacy offline callers with first GNSS state."""
    required = {"gt_speed_mps", "gt_heading_rad"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(
            "dead reckoning needs explicit NavigationInitialState when blackout input has no gt_* fields"
        )
    return NavigationInitialState(
        speed_mps=max(0.0, float(frame.gt_speed_mps.iloc[0])),
        heading_rad=float(frame.gt_heading_rad.iloc[0]),
        gyro_z_bias_rps=0.0,
    )


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
    initial_state: NavigationInitialState | None = None,
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
        "gyro_x_rps", "gyro_y_rps", "gyro_z_rps",
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

    state = initial_state or _legacy_initial_state(frame)
    gyro_bias = state.gyro_z_bias_rps
    yaw_rate = vehicle_gyro[:, 2] - gyro_bias
    speed = np.empty(len(frame), dtype=float)
    heading = np.empty(len(frame), dtype=float)
    east = np.zeros(len(frame), dtype=float)
    north = np.zeros(len(frame), dtype=float)
    # The first speed/heading are available at loss time. After this point, no
    # reference position, heading, or speed is read by the integrator.
    speed[0] = state.speed_mps
    heading[0] = state.heading_rad
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
    *,
    initial_state: NavigationInitialState | None = None,
) -> DeadReckoningResult:
    """NHC replay using Stage 5 CNN speed while retaining classical heading.

    The first model window (2 seconds at the declared model rate) starts from
    the last GNSS-aided speed. Thereafter no GNSS/ground-truth speed is read.
    """
    from .velocity_model import predict_velocity_cnn
    from .preprocessing import VELOCITY_FEATURE_COLUMNS, calibrated_velocity_features

    required = {
        "timestamp_s", "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
        "gyro_x_rps", "gyro_y_rps", "gyro_z_rps",
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
    features = calibrated_velocity_features(frame, calibration)
    raw_windows = np.stack(
        [
            features.loc[index - window_samples + 1 : index, VELOCITY_FEATURE_COLUMNS].to_numpy(np.float32)
            for index in range(window_samples - 1, len(frame))
        ]
    )
    predicted_tail = np.maximum(0.0, predict_velocity_cnn(model, raw_windows, normalization))
    state = initial_state or _legacy_initial_state(frame)
    speed = np.empty(len(frame), dtype=float)
    speed[: window_samples - 1] = state.speed_mps
    speed[window_samples - 1 :] = predicted_tail

    raw_gyro = frame[["gyro_x_rps", "gyro_y_rps", "gyro_z_rps"]].to_numpy(dtype=float)
    vehicle_gyro = apply_mount_rotation(raw_gyro, calibration)
    gyro_bias = state.gyro_z_bias_rps
    heading = np.empty(len(frame), dtype=float)
    east, north = np.zeros(len(frame), dtype=float), np.zeros(len(frame), dtype=float)
    heading[0] = state.heading_rad
    for index in range(1, len(frame)):
        heading[index] = float(wrap_angle(heading[index - 1] + (vehicle_gyro[index, 2] - gyro_bias) * dt[index]))
        distance = 0.5 * (speed[index - 1] + speed[index]) * dt[index]
        east[index] = east[index - 1] + distance * math.sin(heading[index])
        north[index] = north[index - 1] + distance * math.cos(heading[index])
    return DeadReckoningResult(
        timestamp_s=timestamp.tolist(), east_m=east.tolist(), north_m=north.tolist(),
        speed_mps=speed.tolist(), heading_rad=heading.tolist(), gyro_z_bias_rps=gyro_bias,
    )


def runtime_gnss_anchored_dead_reckoning(
    feature_context: pd.DataFrame,
    *,
    blackout_start_index: int,
    model: Any,
    normalization: dict[str, np.ndarray],
    initial_state: NavigationInitialState,
    use_model_residual: bool = True,
) -> RuntimeGnssAnchoredReplay:
    """Replay the deployed GNSS-anchored INS speed state over a blackout.

    ``feature_context`` must contain a contiguous pre-outage sensor history
    followed by the masked-GNSS samples. It deliberately accepts only the
    vehicle-frame IMU channels and timestamps; callers must keep position,
    speed, and heading labels out of this function. The last GNSS-aided state
    is supplied explicitly through ``initial_state``.

    The state equations mirror ``mobile/lib/services/ins_speed_filter.dart``:
    forward acceleration is clipped to ±8 m/s², CNN changes are clipped to
    ±4 m/s² and applied at 0.12 gain, and speed remains within 0–45 m/s.
    """

    from .preprocessing import VELOCITY_FEATURE_COLUMNS
    from .velocity_model import predict_velocity_cnn

    required = {
        "timestamp_s",
        "linear_accel_forward_mps2",
        "gyro_down_rps",
        *VELOCITY_FEATURE_COLUMNS,
    }
    missing = required.difference(feature_context.columns)
    if missing:
        raise ValueError(f"runtime replay frame missing: {sorted(missing)}")
    if not 0 < blackout_start_index < len(feature_context):
        raise ValueError("blackout_start_index must select a non-empty tail after sensor history")

    timestamp = feature_context.timestamp_s.to_numpy(dtype=float)
    dt = np.diff(timestamp, prepend=timestamp[0])
    positive_dt = dt[dt > 0]
    if positive_dt.size == 0:
        raise ValueError("runtime replay timestamps must increase")
    nominal_dt = float(np.median(positive_dt))
    dt[0] = nominal_dt
    if np.any(dt <= 0) or np.any(dt > 0.25 + 1e-9):
        raise ValueError("runtime replay requires contiguous updates at 4 Hz or faster")

    rate_hz = float(np.asarray(normalization["sample_rate_hz"]))
    window_samples = round(2.0 * rate_hz)
    if window_samples < 2:
        raise ValueError("runtime replay model window must contain at least two samples")

    values = feature_context[VELOCITY_FEATURE_COLUMNS].to_numpy(dtype=np.float32)
    blackout_count = len(feature_context) - blackout_start_index
    model_speed = np.full(blackout_count, np.nan, dtype=float)
    valid_window_indices: list[int] = []
    windows: list[np.ndarray] = []
    for index in range(blackout_start_index, len(feature_context)):
        if index < window_samples - 1:
            continue
        window = values[index - window_samples + 1 : index + 1]
        if np.isfinite(window).all():
            valid_window_indices.append(index)
            windows.append(window)
    if windows:
        predicted = predict_velocity_cnn(model, np.stack(windows), normalization)
        for index, speed_value in zip(valid_window_indices, predicted, strict=True):
            model_speed[index - blackout_start_index] = float(speed_value)

    blackout_timestamp = timestamp[blackout_start_index:]
    blackout_dt = dt[blackout_start_index:].copy()
    # The first blackout sample represents the GNSS-aided boundary. Its
    # previous integration interval is irrelevant to the denied-GNSS state.
    blackout_dt[0] = nominal_dt
    forward_acceleration = feature_context.linear_accel_forward_mps2.to_numpy(dtype=float)[
        blackout_start_index:
    ]
    yaw_rate = feature_context.gyro_down_rps.to_numpy(dtype=float)[blackout_start_index:]

    speed = np.empty(blackout_count, dtype=float)
    heading = np.empty(blackout_count, dtype=float)
    east = np.zeros(blackout_count, dtype=float)
    north = np.zeros(blackout_count, dtype=float)
    speed[0] = float(np.clip(initial_state.speed_mps, 0.0, 45.0))
    heading[0] = float(wrap_angle(initial_state.heading_rad))
    previous_model_speed: float | None = None

    for index in range(1, blackout_count):
        interval_s = float(blackout_dt[index])
        acceleration = float(np.clip(forward_acceleration[index], -8.0, 8.0))
        propagated = float(np.clip(speed[index - 1] + acceleration * interval_s, 0.0, 45.0))
        candidate_model_speed = float(model_speed[index])
        model_is_usable = (
            use_model_residual
            and math.isfinite(candidate_model_speed)
            and 0.0 <= candidate_model_speed <= 45.0
        )
        if model_is_usable:
            if previous_model_speed is not None:
                inertial_delta = acceleration * interval_s
                model_delta = candidate_model_speed - previous_model_speed
                residual = float(np.clip(model_delta - inertial_delta, -4.0 * interval_s, 4.0 * interval_s))
                propagated = float(np.clip(propagated + 0.12 * residual, 0.0, 45.0))
            previous_model_speed = candidate_model_speed
        else:
            previous_model_speed = None
        speed[index] = propagated
        heading[index] = float(
            wrap_angle(heading[index - 1] + (yaw_rate[index] - initial_state.gyro_z_bias_rps) * interval_s)
        )
        distance = 0.5 * (speed[index - 1] + speed[index]) * interval_s
        east[index] = east[index - 1] + distance * math.sin(heading[index])
        north[index] = north[index - 1] + distance * math.cos(heading[index])

    return RuntimeGnssAnchoredReplay(
        dead_reckoning=DeadReckoningResult(
            timestamp_s=blackout_timestamp.tolist(),
            east_m=east.tolist(),
            north_m=north.tolist(),
            speed_mps=speed.tolist(),
            heading_rad=heading.tolist(),
            gyro_z_bias_rps=initial_state.gyro_z_bias_rps,
        ),
        model_speed_mps=model_speed.tolist(),
        forward_acceleration_mps2=forward_acceleration.tolist(),
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
