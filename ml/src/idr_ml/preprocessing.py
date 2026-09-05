"""Calibrated, gravity-compensated model features shared by every runtime."""

from __future__ import annotations

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
