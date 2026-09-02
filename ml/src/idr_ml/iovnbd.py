"""IO-VNBD synchronized smartphone/vehicle CSV ingestion.

IO-VNBD's `S-*` phone recordings and `V-*` vehicle recordings use different
clock origins. The synchronized folders pair them, so this module aligns their
elapsed timelines rather than comparing raw timestamp values.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


class IOVNBDFormatError(ValueError):
    """The source CSV lacks a field required by the replay pipeline."""


def _read_csv(path: str | Path, *, nrows: int | None) -> pd.DataFrame:
    """Read modern CSVs and IO-VNBD's legacy Windows-1252 files."""
    try:
        return pd.read_csv(path, nrows=nrows, low_memory=False, encoding="utf-8")
    except UnicodeDecodeError:
        return pd.read_csv(path, nrows=nrows, low_memory=False, encoding="cp1252")


def _normalize_header(name: object) -> str:
    value = str(name).lower().replace("µ", "u").replace("²", "2")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _find_column(frame: pd.DataFrame, aliases: Iterable[str], *, required: bool = True) -> str | None:
    columns = {_normalize_header(column): str(column) for column in frame.columns}
    normalized_aliases = [_normalize_header(alias) for alias in aliases]
    for alias in normalized_aliases:
        if alias in columns:
            return columns[alias]
    for alias in normalized_aliases:
        tokens = set(alias.split())
        matches = [key for key in columns if tokens.issubset(set(key.split()))]
        if len(matches) == 1:
            return columns[matches[0]]
    if required:
        available = ", ".join(map(str, frame.columns[:12]))
        raise IOVNBDFormatError(f"missing {list(aliases)!r}; first columns: {available}")
    return None


def _numeric(frame: pd.DataFrame, aliases: Iterable[str], *, required: bool = True) -> pd.Series:
    column = _find_column(frame, aliases, required=required)
    if column is None:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _elapsed_seconds(frame: pd.DataFrame, *, smartphone: bool) -> pd.Series:
    aliases = (
        ["time since start", "time since start ms", "elapsed time", "timestamp"]
        if smartphone
        else ["time since start of day", "time since start", "timestamp", "time"]
    )
    raw = _numeric(frame, aliases)
    finite = raw.dropna()
    if finite.empty:
        raise IOVNBDFormatError("recording has no finite time field")
    # Android data are elapsed milliseconds; vehicle logger time is seconds.
    median_delta = float(finite.diff().abs().replace(0, np.nan).median())
    is_milliseconds = smartphone or median_delta > 1.0
    if is_milliseconds:
        raw = raw / 1000.0
    return raw - float(raw.loc[finite.index[0]])


def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.replace([np.inf, -np.inf], np.nan)
        .sort_values("elapsed_s")
        .dropna(subset=["elapsed_s"])
        .drop_duplicates("elapsed_s", keep="last")
        .reset_index(drop=True)
    )


def load_smartphone_csv(path: str | Path, *, nrows: int | None = None) -> pd.DataFrame:
    """Parse a `S-*` recording into canonical SI-unit fields."""
    raw = _read_csv(path, nrows=nrows)
    output = pd.DataFrame(
        {
            "elapsed_s": _elapsed_seconds(raw, smartphone=True),
            "gnss_latitude_deg": _numeric(raw, ["gps latitude", "latitude"]),
            "gnss_longitude_deg": _numeric(raw, ["gps longitude", "longitude"]),
            "gnss_speed_mps": _numeric(raw, ["gps speed", "speed gps"]) / 3.6,
            "gnss_accuracy_m": _numeric(raw, ["gps accuracy", "accuracy"], required=False),
            "accel_x_mps2": _numeric(raw, ["accelerometer x", "acceleration x"]),
            "accel_y_mps2": _numeric(raw, ["accelerometer y", "acceleration y"]),
            "accel_z_mps2": _numeric(raw, ["accelerometer z", "acceleration z"]),
            "gravity_x_mps2": _numeric(raw, ["gravity x"], required=False),
            "gravity_y_mps2": _numeric(raw, ["gravity y"], required=False),
            "gravity_z_mps2": _numeric(raw, ["gravity z"], required=False),
            # Source axes are called yaw/pitch/roll. Calibration later maps
            # them to vehicle axes, so the raw semantics are intentionally kept.
            "gyro_z_rps": _numeric(raw, ["gyroscope yaw", "gyro yaw", "gyroscope z"]),
            "gyro_y_rps": _numeric(raw, ["gyroscope pitch", "gyro pitch", "gyroscope y"]),
            "gyro_x_rps": _numeric(raw, ["gyroscope roll", "gyro roll", "gyroscope x"]),
            "mag_x_ut": _numeric(raw, ["magnetic field x", "magnetometer x"]),
            "mag_y_ut": _numeric(raw, ["magnetic field y", "magnetometer y"]),
            "mag_z_ut": _numeric(raw, ["magnetic field z", "magnetometer z"]),
            "phone_yaw_deg": _numeric(raw, ["orientation yaw"], required=False),
            "phone_pitch_deg": _numeric(raw, ["orientation pitch"], required=False),
            "phone_roll_deg": _numeric(raw, ["orientation roll"], required=False),
        }
    )
    return _clean(output)


def load_vehicle_csv(path: str | Path, *, nrows: int | None = None) -> pd.DataFrame:
    """Parse a `V-*` recording used as replay ground truth."""
    raw = _read_csv(path, nrows=nrows)
    output = pd.DataFrame(
        {
            "elapsed_s": _elapsed_seconds(raw, smartphone=False),
            "gt_latitude_deg": _numeric(raw, ["gps latitude", "latitude"]),
            "gt_longitude_deg": _numeric(raw, ["gps longitude", "longitude"]),
            "gt_speed_mps": _numeric(raw, ["gps velocity", "indicated vehicle speed", "vehicle speed"]) / 3.6,
            "gt_heading_deg": _numeric(raw, ["gps heading", "heading"]),
            "gt_yaw_rate_rps": _numeric(raw, ["yaw rate"], required=False) * np.pi / 180.0,
        }
    )
    return _clean(output)


def _interpolate_at(frame: pd.DataFrame, grid_s: np.ndarray) -> pd.DataFrame:
    result: dict[str, np.ndarray] = {"elapsed_s": grid_s}
    source_t = frame.elapsed_s.to_numpy(dtype=float)
    for column in frame.columns:
        if column == "elapsed_s":
            continue
        source_v = frame[column].to_numpy(dtype=float)
        valid = np.isfinite(source_t) & np.isfinite(source_v)
        result[column] = (
            np.interp(grid_s, source_t[valid], source_v[valid])
            if valid.sum() >= 2
            else np.full(grid_s.shape, np.nan)
        )
    return pd.DataFrame(result)


def synchronize_streams(
    smartphone: pd.DataFrame, vehicle: pd.DataFrame, *, target_rate_hz: float = 10.0
) -> pd.DataFrame:
    """Align a pair by elapsed time, then resample it to a fixed rate.

    IO-VNBD phone data are documented at 10 Hz. The default preserves measured
    samples rather than fabricating 100 Hz signal detail; runtime adapters will
    aggregate high-rate mobile/edge IMU input to the model rate later on.
    """
    if target_rate_hz <= 0:
        raise ValueError("target_rate_hz must be positive")
    if smartphone.empty or vehicle.empty:
        raise IOVNBDFormatError("cannot synchronize an empty recording")
    duration_s = min(float(smartphone.elapsed_s.iloc[-1]), float(vehicle.elapsed_s.iloc[-1]))
    if duration_s <= 0:
        raise IOVNBDFormatError("paired recording has no positive overlap")
    grid_s = np.arange(0.0, duration_s, 1.0 / target_rate_hz)
    sensor_grid = _interpolate_at(smartphone, grid_s)
    vehicle_grid = _interpolate_at(vehicle, grid_s).drop(columns="elapsed_s")
    output = pd.concat([sensor_grid, vehicle_grid], axis=1)
    output.insert(0, "timestamp_s", output.pop("elapsed_s"))
    output["gt_heading_rad"] = np.deg2rad(output.pop("gt_heading_deg"))
    output.attrs["sample_rate_hz"] = target_rate_hz
    return output


@dataclass(frozen=True)
class WindowedDataset:
    features: np.ndarray
    labels: np.ndarray
    timestamps_s: np.ndarray
    sample_rate_hz: float


def build_fixed_windows(
    synchronized: pd.DataFrame,
    *,
    window_seconds: float = 2.0,
    stride_seconds: float = 0.2,
    sample_rate_hz: float | None = None,
) -> WindowedDataset:
    """Build [window, time, 9-IMU-channel] tensors and labels at window end."""
    rate = sample_rate_hz or float(synchronized.attrs.get("sample_rate_hz", 10.0))
    window_samples, stride_samples = round(window_seconds * rate), round(stride_seconds * rate)
    if window_samples < 2 or stride_samples < 1:
        raise ValueError("window and stride must yield positive sample counts")
    channels = [
        "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
        "gyro_x_rps", "gyro_y_rps", "gyro_z_rps",
        "mag_x_ut", "mag_y_ut", "mag_z_ut",
    ]
    labels = ["gt_speed_mps", "gt_heading_rad", "gt_latitude_deg", "gt_longitude_deg"]
    missing = set(channels + labels + ["timestamp_s"]).difference(synchronized.columns)
    if missing:
        raise IOVNBDFormatError(f"synchronized stream missing: {sorted(missing)}")
    valid = synchronized.dropna(subset=channels + labels).reset_index(drop=True)
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    times: list[float] = []
    for start in range(0, len(valid) - window_samples + 1, stride_samples):
        stop = start + window_samples
        xs.append(valid.loc[start : stop - 1, channels].to_numpy(dtype=np.float32))
        ys.append(valid.loc[stop - 1, labels].to_numpy(dtype=np.float32))
        times.append(float(valid.loc[stop - 1, "timestamp_s"]))
    if not xs:
        raise IOVNBDFormatError(f"no complete {window_seconds:.1f}s windows at {rate:g} Hz")
    return WindowedDataset(np.stack(xs), np.stack(ys), np.asarray(times), rate)


def load_synchronized_pair(
    smartphone_path: str | Path,
    vehicle_path: str | Path,
    *,
    nrows: int | None = None,
    target_rate_hz: float = 10.0,
) -> pd.DataFrame:
    return synchronize_streams(
        load_smartphone_csv(smartphone_path, nrows=nrows),
        load_vehicle_csv(vehicle_path, nrows=nrows),
        target_rate_hz=target_rate_hz,
    )
