"""Phone-mount calibration from idle gravity and turning heading segments."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd


def wrap_angle(angle_rad: np.ndarray | float) -> np.ndarray | float:
    """Wrap radians to [-pi, pi)."""
    return (np.asarray(angle_rad) + np.pi) % (2.0 * np.pi) - np.pi


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-9:
        raise ValueError("cannot normalize a zero vector")
    return vector / norm


def _align_vectors(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Return the shortest right-handed rotation mapping source onto target."""
    source, target = _unit(source), _unit(target)
    cross = np.cross(source, target)
    sine = float(np.linalg.norm(cross))
    cosine = float(np.clip(np.dot(source, target), -1.0, 1.0))
    if sine < 1e-9:
        return np.eye(3) if cosine > 0 else np.diag([-1.0, -1.0, 1.0])
    skew = np.array(
        [[0.0, -cross[2], cross[1]], [cross[2], 0.0, -cross[0]], [-cross[1], cross[0], 0.0]]
    )
    return np.eye(3) + skew + skew @ skew * ((1.0 - cosine) / (sine * sine))


def _rotation_z(yaw_rad: float) -> np.ndarray:
    c, s = math.cos(yaw_rad), math.sin(yaw_rad)
    # Vehicle x-forward/y-right/z-down is right handed; positive yaw is a
    # clockwise heading change when viewed from above.
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _euler_zyx(rotation: np.ndarray) -> tuple[float, float, float]:
    pitch = math.asin(float(np.clip(-rotation[2, 0], -1.0, 1.0)))
    roll = math.atan2(float(rotation[2, 1]), float(rotation[2, 2]))
    yaw = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
    return roll, pitch, yaw


@dataclass(frozen=True)
class CalibrationResult:
    """The estimated phone-body -> vehicle-frame mount transform."""

    body_to_vehicle: list[list[float]]
    roll_rad: float
    pitch_rad: float
    yaw_rad: float
    heading_sign: int
    static_gravity_residual_deg: float
    compass_heading_rmse_deg: float
    dynamic_turn_correlation: float
    dynamic_turn_rmse_mps2: float
    idle_samples: int
    turning_samples: int
    static_source: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_mount_calibration(
    frame: pd.DataFrame,
    *,
    idle_speed_mps: float = 0.75,
    min_idle_samples: int = 20,
    turn_yaw_rate_rps: float = 0.03,
    turn_speed_mps: float = 2.0,
    min_turning_samples: int = 20,
) -> CalibrationResult:
    """Estimate static pitch/roll and dynamic yaw from synchronized telemetry.

    Static gravity makes yaw unobservable. The dynamic step uses turning
    kinematics: after removing gravity, lateral acceleration should equal
    speed × yaw-rate in the vehicle frame. A 2-D Procrustes fit recovers the
    otherwise unobservable mount yaw. Android compass yaw is retained only as
    a diagnostic because it is commonly distorted inside a vehicle.
    """
    gravity_columns = ["gravity_x_mps2", "gravity_y_mps2", "gravity_z_mps2"]
    required = set(
        gravity_columns
        + [
            "timestamp_s", "gt_speed_mps", "gt_heading_rad", "phone_yaw_deg", "gt_yaw_rate_rps",
            "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
        ]
    )
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"calibration frame missing fields: {sorted(missing)}")

    gravity = frame[gravity_columns].to_numpy(dtype=float)
    gravity_norm = np.linalg.norm(gravity, axis=1)
    idle = np.isfinite(frame.gt_speed_mps.to_numpy(float)) & (frame.gt_speed_mps.to_numpy(float) <= idle_speed_mps)
    physically_valid = np.isfinite(gravity).all(axis=1) & (gravity_norm >= 8.0) & (gravity_norm <= 11.5)
    idle = idle & physically_valid
    static_source = "idle"
    if int(idle.sum()) < min_idle_samples:
        # Gravity is low-pass filtered by Android. Use the most stationary fifth
        # of the run when an explicit stopped period is too short, and surface
        # that fact in metrics instead of hiding the fallback.
        threshold = float(frame.gt_speed_mps.quantile(0.05))
        idle = physically_valid & (frame.gt_speed_mps.to_numpy(float) <= threshold)
        static_source = "low_speed_fallback"
    if int(idle.sum()) < min_idle_samples:
        raise ValueError(f"need at least {min_idle_samples} valid idle gravity samples; found {int(idle.sum())}")
    gravity_phone = np.nanmedian(gravity[idle], axis=0)
    level_rotation = _align_vectors(gravity_phone, np.array([0.0, 0.0, 1.0]))
    corrected_gravity = level_rotation @ _unit(gravity_phone)
    static_residual = math.degrees(math.acos(float(np.clip(corrected_gravity[2], -1.0, 1.0))))

    phone_yaw = np.deg2rad(frame.phone_yaw_deg.to_numpy(dtype=float))
    heading = frame.gt_heading_rad.to_numpy(dtype=float)
    yaw_rate = frame.gt_yaw_rate_rps.to_numpy(dtype=float)
    speed = frame.gt_speed_mps.to_numpy(dtype=float)
    timestamp = frame.timestamp_s.to_numpy(dtype=float)
    raw_accel = frame[["accel_x_mps2", "accel_y_mps2", "accel_z_mps2"]].to_numpy(dtype=float)
    level_linear = (level_rotation @ (raw_accel - gravity).T).T[:, :2]
    longitudinal_accel = np.gradient(speed, timestamp)
    reference_accel = np.column_stack([longitudinal_accel, speed * yaw_rate])
    turning = (
        np.isfinite(phone_yaw)
        & np.isfinite(heading)
        & np.isfinite(yaw_rate)
        & np.isfinite(speed)
        & (np.abs(yaw_rate) >= turn_yaw_rate_rps)
        & (speed >= turn_speed_mps)
        & np.isfinite(level_linear).all(axis=1)
        & np.isfinite(reference_accel).all(axis=1)
    )
    if int(turning.sum()) < min_turning_samples:
        raise ValueError(f"need at least {min_turning_samples} turning samples; found {int(turning.sum())}")
    selected_phone, selected_heading = phone_yaw[turning], heading[turning]
    candidates: list[tuple[float, int, float, np.ndarray]] = []
    for sign in (1, -1):
        residual = wrap_angle(selected_heading - sign * selected_phone)
        yaw_offset = float(np.angle(np.mean(np.exp(1j * residual))))
        final_residual = wrap_angle(residual - yaw_offset)
        rmse = float(np.sqrt(np.mean(np.square(final_residual))))
        candidates.append((rmse, sign, yaw_offset, np.asarray(final_residual)))
    compass_rmse, sign, _, _ = min(candidates, key=lambda item: item[0])

    source, reference = level_linear[turning], reference_accel[turning]
    weights = np.clip(np.linalg.norm(reference, axis=1), 0.1, 5.0)
    dot = float(np.sum(weights * np.sum(source * reference, axis=1)))
    cross = float(np.sum(weights * (source[:, 0] * reference[:, 1] - source[:, 1] * reference[:, 0])))
    yaw_offset = math.atan2(cross, dot)
    rotation_2d = _rotation_z(yaw_offset)[:2, :2]
    predicted = source @ rotation_2d.T
    source_norm = np.linalg.norm(predicted, axis=1)
    reference_norm = np.linalg.norm(reference, axis=1)
    valid_norm = (source_norm > 0.1) & (reference_norm > 0.1)
    if int(valid_norm.sum()) < min_turning_samples:
        raise ValueError("turning acceleration vectors have insufficient magnitude")
    cosine = np.sum(predicted[valid_norm] * reference[valid_norm], axis=1) / (
        source_norm[valid_norm] * reference_norm[valid_norm]
    )
    turn_correlation = float(np.average(cosine, weights=weights[valid_norm]))
    turn_rmse = float(np.sqrt(np.mean(np.square(predicted - reference))))

    body_to_vehicle = _rotation_z(yaw_offset) @ level_rotation
    roll, pitch, yaw = _euler_zyx(body_to_vehicle)
    return CalibrationResult(
        body_to_vehicle=body_to_vehicle.round(10).tolist(),
        roll_rad=roll,
        pitch_rad=pitch,
        yaw_rad=yaw,
        heading_sign=sign,
        static_gravity_residual_deg=static_residual,
        compass_heading_rmse_deg=math.degrees(compass_rmse),
        dynamic_turn_correlation=turn_correlation,
        dynamic_turn_rmse_mps2=turn_rmse,
        idle_samples=int(idle.sum()),
        turning_samples=int(turning.sum()),
        static_source=static_source,
    )


def apply_mount_rotation(vectors: np.ndarray, calibration: CalibrationResult) -> np.ndarray:
    """Rotate N×3 phone-body vectors into the calibrated vehicle frame."""
    rotation = np.asarray(calibration.body_to_vehicle, dtype=float)
    values = np.asarray(vectors, dtype=float)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("vectors must have shape [N, 3]")
    return values @ rotation.T
