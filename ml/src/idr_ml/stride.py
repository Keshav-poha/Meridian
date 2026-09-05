"""STRIDE smartphone-drive ingestion for velocity and vibration training.

STRIDE is used as a second, independent phone domain. Its Android recordings
contain linear acceleration, gravity, gyroscope, magnetometer, and GNSS speed
in separate CSV streams. The loader makes those fields conform to MERIDIAN's
10 Hz phone IMU contract without pretending that its phone GNSS is a vehicle
odometry reference.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


class StrideFormatError(ValueError):
    """The selected STRIDE session cannot provide the required phone signals."""


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise StrideFormatError(f"missing STRIDE sensor file: {path}")
    result = pd.read_csv(path)
    required = {"seconds_elapsed", "x", "y", "z"}
    missing = required.difference(result.columns)
    if missing:
        raise StrideFormatError(f"{path.name} missing columns: {sorted(missing)}")
    result = result[["seconds_elapsed", "x", "y", "z"]].copy()
    result.columns = ["timestamp_s", "x", "y", "z"]
    for column in result.columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return (
        result.replace([np.inf, -np.inf], np.nan)
        .dropna(subset=["timestamp_s"])
        .sort_values("timestamp_s")
        .drop_duplicates("timestamp_s", keep="last")
        .reset_index(drop=True)
    )


def _read_location(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise StrideFormatError(f"missing STRIDE location file: {path}")
    source = pd.read_csv(path)
    required = {"seconds_elapsed", "speed", "bearing", "longitude", "latitude"}
    missing = required.difference(source.columns)
    if missing:
        raise StrideFormatError(f"Location.csv missing columns: {sorted(missing)}")
    columns = ["seconds_elapsed", "speed", "bearing", "longitude", "latitude"]
    if "horizontalAccuracy" in source.columns:
        columns.append("horizontalAccuracy")
    result = source[columns].copy()
    result = result.rename(columns={"seconds_elapsed": "timestamp_s", "horizontalAccuracy": "accuracy_m"})
    if "accuracy_m" not in result:
        result["accuracy_m"] = np.nan
    for column in result.columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return (
        result.replace([np.inf, -np.inf], np.nan)
        .dropna(subset=["timestamp_s"])
        .sort_values("timestamp_s")
        .drop_duplicates("timestamp_s", keep="last")
        .reset_index(drop=True)
    )


def _resample(
    frame: pd.DataFrame,
    grid: np.ndarray,
    *,
    maximum_gap_s: float,
) -> pd.DataFrame:
    result: dict[str, np.ndarray] = {"timestamp_s": grid}
    time = frame.timestamp_s.to_numpy(dtype=float)
    for column in frame.columns:
        if column == "timestamp_s":
            continue
        values = frame[column].to_numpy(dtype=float)
        valid = np.isfinite(time) & np.isfinite(values)
        if int(valid.sum()) < 2:
            result[column] = np.full(grid.shape, np.nan)
            continue
        valid_time, valid_values = time[valid], values[valid]
        right = np.searchsorted(valid_time, grid, side="left")
        left = right - 1
        exact = (right < len(valid_time)) & np.isclose(
            valid_time[np.minimum(right, len(valid_time) - 1)], grid, atol=1e-6
        )
        between = (left >= 0) & (right < len(valid_time))
        short_gap = np.zeros(grid.shape, dtype=bool)
        short_gap[between] = (
            valid_time[right[between]] - valid_time[left[between]] <= maximum_gap_s
        )
        interpolated = np.interp(grid, valid_time, valid_values)
        result[column] = np.where(exact | short_gap, interpolated, np.nan)
    return pd.DataFrame(result)


def load_stride_session(
    session: str | Path,
    *,
    target_rate_hz: float = 10.0,
    max_imu_gap_s: float = 0.15,
    max_location_gap_s: float = 1.5,
) -> pd.DataFrame:
    """Load one STRIDE session into the canonical phone/vehicle-label frame.

    The GPS speed is retained as a lower-confidence supervisory label only.
    It never becomes a claimed external speedometer or a runtime input.
    """

    root = Path(session)
    if target_rate_hz <= 0.0:
        raise ValueError("target_rate_hz must be positive")
    linear = _read_csv(root / "Accelerometer.csv")
    gravity = _read_csv(root / "Gravity.csv")
    gyroscope = _read_csv(root / "Gyroscope.csv")
    magnetometer = _read_csv(root / "Magnetometer.csv")
    location = _read_location(root / "Location.csv")
    streams = (linear, gravity, gyroscope, magnetometer, location)
    start = max(float(stream.timestamp_s.min()) for stream in streams)
    end = min(float(stream.timestamp_s.max()) for stream in streams)
    if end - start < 3.0:
        raise StrideFormatError(f"{root} has less than three seconds of overlapping sensor data")
    grid = np.arange(start, end, 1.0 / target_rate_hz)
    linear_grid = _resample(linear, grid, maximum_gap_s=max_imu_gap_s)
    gravity_grid = _resample(gravity, grid, maximum_gap_s=max_imu_gap_s)
    gyro_grid = _resample(gyroscope, grid, maximum_gap_s=max_imu_gap_s)
    mag_grid = _resample(magnetometer, grid, maximum_gap_s=max_imu_gap_s)
    location_grid = _resample(location, grid, maximum_gap_s=max_location_gap_s)

    # STRIDE's accelerometer stream is Android linear acceleration (gravity is
    # supplied separately). Recombine them so the shared preprocessor follows
    # the same accel-minus-gravity path as a live phone.
    output = pd.DataFrame(
        {
            "timestamp_s": grid,
            "accel_x_mps2": linear_grid.x + gravity_grid.x,
            "accel_y_mps2": linear_grid.y + gravity_grid.y,
            "accel_z_mps2": linear_grid.z + gravity_grid.z,
            "gravity_x_mps2": gravity_grid.x,
            "gravity_y_mps2": gravity_grid.y,
            "gravity_z_mps2": gravity_grid.z,
            "gyro_x_rps": gyro_grid.x,
            "gyro_y_rps": gyro_grid.y,
            "gyro_z_rps": gyro_grid.z,
            "mag_x_ut": mag_grid.x,
            "mag_y_ut": mag_grid.y,
            "mag_z_ut": mag_grid.z,
            "gt_speed_mps": location_grid.speed,
            "gt_heading_rad": np.deg2rad(location_grid.bearing),
            "gt_latitude_deg": location_grid.latitude,
            "gt_longitude_deg": location_grid.longitude,
            "gnss_accuracy_m": location_grid.accuracy_m,
        }
    )
    heading = output.gt_heading_rad.to_numpy(dtype=float)
    valid_heading = np.isfinite(heading)
    unwrapped = np.full(heading.shape, np.nan)
    if int(valid_heading.sum()) >= 2:
        unwrapped[valid_heading] = np.unwrap(heading[valid_heading])
        output["gt_yaw_rate_rps"] = np.gradient(unwrapped, grid)
    else:
        output["gt_yaw_rate_rps"] = np.nan
    output.attrs["sample_rate_hz"] = target_rate_hz
    output.attrs["dataset"] = "STRIDE"
    output.attrs["speed_label"] = "smartphone_gnss_speed_mps"
    output.attrs["source_session"] = str(root)
    return output


def discover_stride_driving_sessions(root: str | Path) -> list[Path]:
    """Return labelled normal/aggressive/slow sessions, excluding anomalies."""

    driving_root = Path(root) / "Driving Behaviour"
    if not driving_root.is_dir():
        raise StrideFormatError(f"STRIDE Driving Behaviour directory not found: {driving_root}")
    return sorted(path for path in driving_root.iterdir() if path.is_dir())
