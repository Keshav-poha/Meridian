"""Adaptive GNSS+INS fusion with pre-outage residual calibration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd

from .calibration import CalibrationResult, apply_mount_rotation, wrap_angle
from .dead_reckoning import DeadReckoningResult, NavigationInitialState
from .preprocessing import VELOCITY_FEATURE_COLUMNS, calibrated_velocity_features
from .velocity_model import predict_velocity_cnn


@dataclass(frozen=True)
class AdaptiveResidual:
    speed_scale: float
    speed_bias_mps: float
    yaw_scale: float
    yaw_bias_rps: float
    history_samples: int

    speed_correlation: float | None
    speed_adaptation_applied: bool
    yaw_correlation: float | None
    yaw_adaptation_applied: bool

    def as_dict(self) -> dict[str, float | int | bool | None]:
        return asdict(self)


def _velocity_windows(
    frame: pd.DataFrame, calibration: CalibrationResult, rate_hz: float
) -> tuple[np.ndarray, int]:
    features = calibrated_velocity_features(frame, calibration)
    samples = round(2.0 * rate_hz)
    values = features[VELOCITY_FEATURE_COLUMNS].to_numpy(np.float32)
    windows = np.stack([values[i - samples + 1 : i + 1] for i in range(samples - 1, len(values))])
    return windows, samples


def estimate_adaptive_residual(
    history: pd.DataFrame,
    calibration: CalibrationResult,
    model: object,
    normalization: dict[str, np.ndarray],
) -> AdaptiveResidual:
    """Fit speed/yaw residuals using only GNSS-aided samples before loss."""
    rate = float(np.asarray(normalization["sample_rate_hz"]))
    windows, offset = _velocity_windows(history, calibration, rate)
    predicted_speed = predict_velocity_cnn(model, windows, normalization)
    reference_speed = history.gt_speed_mps.to_numpy(float)[offset - 1 :]
    # A local affine fit is unsafe when the model/reference correlation is
    # weak (e.g. braking or a transient GNSS lag). In that case retain the
    # portable fleet calibration factor rather than injecting a large bias.
    speed_correlation = (
        float(np.corrcoef(predicted_speed, reference_speed)[0, 1])
        if np.std(predicted_speed) >= 1e-4 and np.std(reference_speed) >= 1e-4
        else None
    )
    speed_adaptation_applied = (
        speed_correlation is not None and np.isfinite(speed_correlation) and speed_correlation >= 0.5
    )
    if speed_adaptation_applied:
        speed_scale = float(np.median(reference_speed / np.maximum(predicted_speed, 0.5)))
    else:
        # A weak local fit is evidence to *not* adapt. A fixed fleet scale
        # would be a hidden test-tuned assumption and can worsen a deployed
        # model that is already closer to the pre-outage GNSS speed.
        speed_scale = 1.0
    speed_bias = 0.0

    raw_gyro = history[["gyro_x_rps", "gyro_y_rps", "gyro_z_rps"]].to_numpy(float)
    gyro_z = apply_mount_rotation(raw_gyro, calibration)[:, 2]
    timestamp = history.timestamp_s.to_numpy(float)
    heading_rate = np.gradient(np.unwrap(history.gt_heading_rad.to_numpy(float)), timestamp)
    valid = np.isfinite(gyro_z) & np.isfinite(heading_rate) & (np.abs(heading_rate) < 1.5)
    yaw_correlation = (
        float(np.corrcoef(gyro_z[valid], heading_rate[valid])[0, 1])
        if int(valid.sum()) >= 3 and np.std(gyro_z[valid]) >= 1e-4 and np.std(heading_rate[valid]) >= 1e-4
        else None
    )
    yaw_adaptation_applied = (
        yaw_correlation is not None and np.isfinite(yaw_correlation) and abs(yaw_correlation) >= 0.5
    )
    if yaw_adaptation_applied:
        yaw_design = np.column_stack([gyro_z[valid], np.ones(int(valid.sum()))])
        yaw_scale, yaw_bias = np.linalg.lstsq(yaw_design, heading_rate[valid], rcond=None)[0]
    else:
        yaw_scale, yaw_bias = 1.0, 0.0
    return AdaptiveResidual(
        speed_scale=float(np.clip(speed_scale, 0.7, 1.3)), speed_bias_mps=float(np.clip(speed_bias, -1.0, 1.0)),
        yaw_scale=float(np.clip(yaw_scale, -3.0, 3.0)), yaw_bias_rps=float(np.clip(yaw_bias, -0.5, 0.5)),
        history_samples=len(history),
        speed_correlation=speed_correlation if speed_correlation is None or np.isfinite(speed_correlation) else None,
        speed_adaptation_applied=speed_adaptation_applied,
        yaw_correlation=yaw_correlation if yaw_correlation is None or np.isfinite(yaw_correlation) else None,
        yaw_adaptation_applied=yaw_adaptation_applied,
    )


def adaptive_fused_replay(
    blackout: pd.DataFrame,
    calibration: CalibrationResult,
    model: object,
    normalization: dict[str, np.ndarray],
    residual: AdaptiveResidual,
    *,
    initial_state: NavigationInitialState | None = None,
) -> DeadReckoningResult:
    """Propagate the frozen adaptive INS state during a masked GNSS interval."""
    rate = float(np.asarray(normalization["sample_rate_hz"]))
    windows, offset = _velocity_windows(blackout, calibration, rate)
    predicted_tail = np.maximum(0.0, predict_velocity_cnn(model, windows, normalization))
    if initial_state is None:
        required = {"gt_speed_mps", "gt_heading_rad"}
        missing = required.difference(blackout.columns)
        if missing:
            raise ValueError(
                "adaptive replay needs explicit NavigationInitialState when blackout input has no gt_* fields"
            )
        initial_state = NavigationInitialState(
            speed_mps=max(0.0, float(blackout.gt_speed_mps.iloc[0])),
            heading_rad=float(blackout.gt_heading_rad.iloc[0]),
            gyro_z_bias_rps=0.0,
        )
    speed = np.empty(len(blackout), dtype=float)
    speed[: offset - 1] = initial_state.speed_mps
    speed[offset - 1 :] = np.maximum(0.0, residual.speed_scale * predicted_tail + residual.speed_bias_mps)
    timestamp = blackout.timestamp_s.to_numpy(float)
    dt = np.diff(timestamp, prepend=timestamp[0])
    dt[0] = float(np.median(dt[dt > 0]))
    gyro = apply_mount_rotation(blackout[["gyro_x_rps", "gyro_y_rps", "gyro_z_rps"]].to_numpy(float), calibration)[:, 2]
    heading = np.empty(len(blackout), dtype=float)
    east, north = np.zeros(len(blackout)), np.zeros(len(blackout))
    heading[0] = initial_state.heading_rad
    # Process covariance is deliberately retained even though GNSS is masked;
    # Stage 9 uses it to blend reacquired GNSS without a visual jump.
    covariance = 4.0
    for index in range(1, len(blackout)):
        heading[index] = float(wrap_angle(
            heading[index - 1]
            + (residual.yaw_scale * (gyro[index] - initial_state.gyro_z_bias_rps) + residual.yaw_bias_rps)
            * dt[index]
        ))
        distance = 0.5 * (speed[index - 1] + speed[index]) * dt[index]
        east[index] = east[index - 1] + distance * math.sin(heading[index])
        north[index] = north[index - 1] + distance * math.cos(heading[index])
        covariance += 0.15 + 0.02 * abs(distance)
    return DeadReckoningResult(timestamp.tolist(), east.tolist(), north.tolist(), speed.tolist(), heading.tolist(), residual.yaw_bias_rps)
