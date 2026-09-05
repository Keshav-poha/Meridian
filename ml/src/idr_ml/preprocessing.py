"""Calibrated, gravity-compensated model features shared by every runtime."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from .calibration import CalibrationResult, apply_mount_rotation


VELOCITY_FEATURE_COLUMNS = [
    "linear_accel_forward_mps2",
    "linear_accel_right_mps2",
    "linear_accel_down_mps2",
    "gyro_forward_rps",
    "gyro_right_rps",
    "gyro_down_rps",
    "mag_forward_unit",
    "mag_right_unit",
    "mag_down_unit",
]


@dataclass(frozen=True)
class KinematicMountAlignment:
    """Evidence recorded by the runtime-equivalent phone-mount fit.

    In-car magnetic fields are routinely distorted by the dashboard, speakers,
    and charging hardware.  A fixed phone mount can instead be resolved from
    measured vehicle kinematics: after gravity leveling, the horizontal IMU
    acceleration must agree with GNSS-derived longitudinal and lateral
    acceleration during turns.  The mobile implementation follows the same
    fit and refuses to use an unverified yaw transform.
    """

    yaw_rad: float
    turning_samples: int
    dynamic_turn_correlation: float
    heading_coverage_deg: float


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-9:
        raise ValueError("cannot normalize a zero vector")
    return vector / norm


def _align_to_down(gravity_body: np.ndarray) -> np.ndarray:
    """Return a right-handed rotation that maps phone gravity to vehicle down."""

    source, target = _unit(gravity_body), np.array([0.0, 0.0, 1.0])
    cross = np.cross(source, target)
    sine = float(np.linalg.norm(cross))
    cosine = float(np.clip(np.dot(source, target), -1.0, 1.0))
    if sine < 1e-9:
        return np.eye(3) if cosine >= 0.0 else np.diag([-1.0, -1.0, 1.0])
    skew = np.array(
        [[0.0, -cross[2], cross[1]], [cross[2], 0.0, -cross[0]], [-cross[1], cross[0], 0.0]]
    )
    return np.eye(3) + skew + skew @ skew * ((1.0 - cosine) / (sine * sine))


def _rotation_z(yaw_rad: float) -> np.ndarray:
    cosine, sine = math.cos(yaw_rad), math.sin(yaw_rad)
    return np.array(
        [[cosine, -sine, 0.0], [sine, cosine, 0.0], [0.0, 0.0, 1.0]]
    )


def _wrap_angle(values: np.ndarray | float) -> np.ndarray:
    return (values + np.pi) % (2.0 * np.pi) - np.pi


def _runtime_gravity_estimate(
    acceleration: np.ndarray,
    gyroscope: np.ndarray,
    timestamp_s: np.ndarray,
) -> np.ndarray:
    """Replicate the phone's complementary gravity estimator at 10 Hz."""

    if acceleration.shape != gyroscope.shape or acceleration.shape[1:] != (3,):
        raise ValueError("acceleration and gyroscope must both have shape [N, 3]")
    if len(timestamp_s) != len(acceleration):
        raise ValueError("gravity-estimator timestamps do not match sensor rows")
    estimate = np.empty_like(acceleration, dtype=float)
    previous: np.ndarray | None = None
    previous_timestamp: float | None = None
    gravity_mps2 = 9.80665
    for index, (raw_acceleration, gyro, timestamp) in enumerate(
        zip(acceleration, gyroscope, timestamp_s)
    ):
        if not np.isfinite(raw_acceleration).all() or not np.isfinite(gyro).all():
            estimate[index] = np.nan
            previous = None
            previous_timestamp = None
            continue
        if previous is None:
            norm = float(np.linalg.norm(raw_acceleration))
            estimate[index] = raw_acceleration * (gravity_mps2 / norm) if norm >= 1e-9 else np.nan
            previous = estimate[index] if np.isfinite(estimate[index]).all() else None
            previous_timestamp = float(timestamp)
            continue
        dt = min(0.1, max(0.0, float(timestamp) - float(previous_timestamp)))
        propagated_raw = previous + np.cross(previous, gyro) * dt
        propagated_norm = float(np.linalg.norm(propagated_raw))
        if propagated_norm < 1e-9:
            estimate[index] = np.nan
            previous = None
            previous_timestamp = float(timestamp)
            continue
        propagated = propagated_raw * (gravity_mps2 / propagated_norm)
        acceleration_norm = float(np.linalg.norm(raw_acceleration))
        if abs(acceleration_norm - gravity_mps2) <= 0.75 and float(np.linalg.norm(gyro)) <= 0.35:
            correction = raw_acceleration * (gravity_mps2 / max(acceleration_norm, 1e-9))
            blended = 0.96 * propagated + 0.04 * correction
            estimate[index] = blended * (gravity_mps2 / max(float(np.linalg.norm(blended)), 1e-9))
        else:
            estimate[index] = propagated
        previous = estimate[index]
        previous_timestamp = float(timestamp)
    return estimate


def _circular_spread(values: np.ndarray) -> float:
    if len(values) < 2:
        return 0.0
    delta = _wrap_angle(values[:, None] - values[None, :])
    return float(np.max(np.abs(delta)))


def _fit_kinematic_yaw(
    source: np.ndarray,
    reference: np.ndarray,
) -> tuple[float, float]:
    """Fit yaw and return its weighted directional agreement score."""

    if source.shape != reference.shape or source.ndim != 2 or source.shape[1] != 2:
        raise ValueError("kinematic yaw fit needs matching [N, 2] vectors")
    weights = np.clip(np.linalg.norm(reference, axis=1), 0.1, 5.0)
    dot = float(np.sum(weights * np.sum(source * reference, axis=1)))
    cross = float(np.sum(weights * (source[:, 0] * reference[:, 1] - source[:, 1] * reference[:, 0])))
    yaw = math.atan2(cross, dot)
    rotated = source @ _rotation_z(yaw)[:2, :2].T
    source_norm = np.linalg.norm(rotated, axis=1)
    reference_norm = np.linalg.norm(reference, axis=1)
    valid = (source_norm > 0.1) & (reference_norm > 0.1)
    if int(valid.sum()) < 1:
        raise ValueError("kinematic yaw vectors have insufficient magnitude")
    agreement = np.sum(rotated[valid] * reference[valid], axis=1) / (
        source_norm[valid] * reference_norm[valid]
    )
    return yaw, float(np.average(agreement, weights=weights[valid]))


def runtime_equivalent_velocity_features(
    frame: pd.DataFrame,
    *,
    heading_column: str = "gt_heading_rad",
    speed_column: str = "gt_speed_mps",
    timestamp_column: str = "timestamp_s",
    minimum_speed_mps: float = 2.0,
    minimum_turning_samples: int = 20,
    minimum_turn_correlation: float = 0.2,
    minimum_heading_coverage_deg: float = 20.0,
    gnss_reference_interval_s: float = 0.8,
    maximum_gnss_reference_gap_s: float = 2.5,
) -> tuple[pd.DataFrame, KinematicMountAlignment]:
    """Build the exact gravity/kinematic vehicle frame used on the phone.

    This is deliberately independent of the magnetometer for mount yaw.  It
    uses GNSS speed and course *only while GNSS is available* to establish the
    fixed orientation; the resulting transform is then suitable for an IMU
    blackout.  The CNN receives no GNSS value as a feature.
    """

    required = {
        "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
        "gyro_x_rps", "gyro_y_rps", "gyro_z_rps",
        "mag_x_ut", "mag_y_ut", "mag_z_ut",
        heading_column, speed_column, timestamp_column,
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"live-equivalent preprocessing frame missing: {sorted(missing)}")
    if (
        minimum_turning_samples < 1
        or not -1.0 <= minimum_turn_correlation <= 1.0
        or minimum_heading_coverage_deg <= 0.0
        or gnss_reference_interval_s <= 0.0
        or maximum_gnss_reference_gap_s < gnss_reference_interval_s
    ):
        raise ValueError("runtime mount-alignment thresholds are invalid")

    acceleration = frame[["accel_x_mps2", "accel_y_mps2", "accel_z_mps2"]].to_numpy(float)
    gyroscope = frame[["gyro_x_rps", "gyro_y_rps", "gyro_z_rps"]].to_numpy(float)
    magnetometer = frame[["mag_x_ut", "mag_y_ut", "mag_z_ut"]].to_numpy(float)
    heading = frame[heading_column].to_numpy(float)
    speed = frame[speed_column].to_numpy(float)
    timestamp = frame[timestamp_column].to_numpy(float)
    estimated_gravity = _runtime_gravity_estimate(acceleration, gyroscope, timestamp)
    gravity_norm = np.linalg.norm(estimated_gravity, axis=1)
    gravity_valid = (
        np.isfinite(estimated_gravity).all(axis=1)
        & (gravity_norm >= 8.0)
        & (gravity_norm <= 11.5)
    )
    level = np.stack(
        [_align_to_down(value) if valid else np.eye(3) for value, valid in zip(estimated_gravity, gravity_valid)]
    )
    linear_level = np.einsum("nij,nj->ni", level, acceleration - estimated_gravity)

    source_vectors: list[np.ndarray] = []
    reference_vectors: list[np.ndarray] = []
    observed_headings: list[float] = []
    previous_reference: tuple[float, float, float] | None = None
    for index, (sample_time, sample_speed, sample_heading) in enumerate(
        zip(timestamp, speed, heading)
    ):
        if not (
            gravity_valid[index]
            and np.isfinite(sample_time)
            and np.isfinite(sample_speed)
            and np.isfinite(sample_heading)
            and sample_speed >= minimum_speed_mps
        ):
            continue
        if previous_reference is None:
            previous_reference = (float(sample_time), float(sample_speed), float(sample_heading))
            continue
        previous_time, previous_speed, previous_heading = previous_reference
        elapsed_s = float(sample_time) - previous_time
        if elapsed_s < gnss_reference_interval_s:
            continue
        previous_reference = (float(sample_time), float(sample_speed), float(sample_heading))
        if elapsed_s > maximum_gnss_reference_gap_s:
            continue
        yaw_rate = float(_wrap_angle(sample_heading - previous_heading)) / elapsed_s
        reference = np.array(
            [
                (float(sample_speed) - previous_speed) / elapsed_s,
                ((float(sample_speed) + previous_speed) * 0.5) * yaw_rate,
            ]
        )
        source = linear_level[index, :2]
        if (
            abs(yaw_rate) < 0.03
            or not np.isfinite(source).all()
            or not np.isfinite(reference).all()
            or float(np.linalg.norm(source)) < 0.1
            or float(np.linalg.norm(reference)) < 0.1
        ):
            continue
        source_vectors.append(source)
        reference_vectors.append(reference)
        observed_headings.append(float(sample_heading))
    if len(source_vectors) < minimum_turning_samples:
        raise ValueError(
            f"need at least {minimum_turning_samples} kinematic turning samples; "
            f"found {len(source_vectors)}"
        )
    source_matrix = np.stack(source_vectors)
    reference_matrix = np.stack(reference_vectors)
    yaw, turn_correlation = _fit_kinematic_yaw(source_matrix, reference_matrix)
    heading_coverage_deg = math.degrees(_circular_spread(np.asarray(observed_headings)))
    if turn_correlation < minimum_turn_correlation:
        raise ValueError(
            f"kinematic yaw agreement {turn_correlation:.2f} is below the "
            f"{minimum_turn_correlation:.2f} mobile-calibration limit"
        )
    if heading_coverage_deg < minimum_heading_coverage_deg:
        raise ValueError(
            f"kinematic heading coverage {heading_coverage_deg:.1f}° is below the "
            f"{minimum_heading_coverage_deg:.1f}° mobile-calibration limit"
        )

    body_to_vehicle = np.einsum("ij,njk->nik", _rotation_z(yaw), level)
    linear_vehicle = np.einsum("nij,nj->ni", body_to_vehicle, acceleration - estimated_gravity)
    gyro_vehicle = np.einsum("nij,nj->ni", body_to_vehicle, gyroscope)
    magnetic_vehicle = np.einsum("nij,nj->ni", body_to_vehicle, magnetometer)
    magnetic_unit = np.divide(
        magnetic_vehicle,
        np.linalg.norm(magnetic_vehicle, axis=1, keepdims=True),
        out=np.zeros_like(magnetic_vehicle),
        where=np.linalg.norm(magnetic_vehicle, axis=1, keepdims=True) > 1e-6,
    )
    output = frame.copy()
    output.attrs = frame.attrs.copy()
    output[VELOCITY_FEATURE_COLUMNS] = np.hstack(
        [linear_vehicle, gyro_vehicle, magnetic_unit]
    )
    alignment = KinematicMountAlignment(
        yaw_rad=yaw,
        turning_samples=len(source_vectors),
        dynamic_turn_correlation=turn_correlation,
        heading_coverage_deg=heading_coverage_deg,
    )
    output.attrs["kinematic_mount_alignment"] = {
        "yaw_rad": alignment.yaw_rad,
        "turning_samples": alignment.turning_samples,
        "dynamic_turn_correlation": alignment.dynamic_turn_correlation,
        "heading_coverage_deg": alignment.heading_coverage_deg,
    }
    return output, alignment


def calibrated_velocity_features(
    frame: pd.DataFrame, calibration: CalibrationResult
) -> pd.DataFrame:
    """Append the model's vehicle-frame input features to synchronized data.

    Accelerometer readings contain both vehicle acceleration and gravity. The
    calibration stage provides the body-to-vehicle rotation, while Android's
    gravity estimate supplies the gravity vector to remove. Magnetic field is
    rotated then normalized to direction, preventing field-strength changes
    between devices/locations from acting like speed evidence.
    """
    required = {
        "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
        "gravity_x_mps2", "gravity_y_mps2", "gravity_z_mps2",
        "gyro_x_rps", "gyro_y_rps", "gyro_z_rps",
        "mag_x_ut", "mag_y_ut", "mag_z_ut",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"feature preprocessing frame missing: {sorted(missing)}")

    acceleration = frame[["accel_x_mps2", "accel_y_mps2", "accel_z_mps2"]].to_numpy(float)
    gravity = frame[["gravity_x_mps2", "gravity_y_mps2", "gravity_z_mps2"]].to_numpy(float)
    gyroscope = frame[["gyro_x_rps", "gyro_y_rps", "gyro_z_rps"]].to_numpy(float)
    magnetometer = frame[["mag_x_ut", "mag_y_ut", "mag_z_ut"]].to_numpy(float)

    linear_vehicle = apply_mount_rotation(acceleration - gravity, calibration)
    gyro_vehicle = apply_mount_rotation(gyroscope, calibration)
    magnetic_vehicle = apply_mount_rotation(magnetometer, calibration)
    magnetic_norm = np.linalg.norm(magnetic_vehicle, axis=1, keepdims=True)
    magnetic_unit = np.divide(
        magnetic_vehicle,
        magnetic_norm,
        out=np.zeros_like(magnetic_vehicle),
        where=magnetic_norm > 1e-6,
    )

    output = frame.copy()
    output.attrs = frame.attrs.copy()
    output[VELOCITY_FEATURE_COLUMNS] = np.hstack(
        [linear_vehicle, gyro_vehicle, magnetic_unit]
    )
    return output
