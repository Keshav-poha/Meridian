"""IO-VNBD synchronized smartphone/vehicle CSV ingestion.

IO-VNBD's `S-*` phone recordings and `V-*` vehicle recordings use different
clock origins. The synchronized folders pair them, so this module aligns their
elapsed timelines rather than comparing raw timestamp values.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


class IOVNBDFormatError(ValueError):
    """The source CSV lacks a field required by the replay pipeline."""


@dataclass(frozen=True)
class ClockOffsetEstimate:
    """Auditable mapping from the phone clock to the vehicle clock.

    ``offset_s`` uses one explicit convention throughout this module:
    ``vehicle_time_s = phone_time_s + offset_s``.
    """

    offset_s: float
    method: str
    correlation: float | None
    zero_offset_correlation: float | None
    correlation_improvement: float | None
    median_absolute_speed_error_mps: float | None
    matched_samples: int
    candidate_offsets_tested: int
    validation_passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "vehicle_time_s_equals_phone_time_s_plus_offset_s": self.offset_s,
            "speed_correlation": self.correlation,
            "zero_offset_speed_correlation": self.zero_offset_correlation,
            "correlation_improvement": self.correlation_improvement,
            "median_absolute_speed_error_mps": self.median_absolute_speed_error_mps,
            "matched_samples": self.matched_samples,
            "candidate_offsets_tested": self.candidate_offsets_tested,
            "validation_passed": self.validation_passed,
        }


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


def _speed_to_mps(values: pd.Series, *, unit: str) -> pd.Series:
    normalized = unit.strip().lower().replace("/", "_")
    if normalized in {"mps", "m_s", "m_s_1", "meters_per_second", "metres_per_second"}:
        return values
    if normalized in {"kmh", "km_h", "kph", "kilometers_per_hour", "kilometres_per_hour"}:
        return values / 3.6
    raise IOVNBDFormatError(f"unsupported smartphone GNSS speed unit: {unit!r}; use 'mps' or 'kmh'")


def load_smartphone_csv(
    path: str | Path,
    *,
    nrows: int | None = None,
    gnss_speed_unit: str = "mps",
) -> pd.DataFrame:
    """Parse an IO-VNBD `S-*` recording into canonical SI-unit fields.

    The checked IO-VNBD phone logs label GPS speed as ``Kmh`` but the recorded
    values are metres/second (for example 5.84 aligns with a 5.46 m/s vehicle
    fix). ``mps`` is therefore the deliberate default for this dataset. Pass
    ``gnss_speed_unit='kmh'`` only for a separately sourced, genuinely
    kilometre-per-hour phone log.
    """
    raw = _read_csv(path, nrows=nrows)
    gnss_speed = _speed_to_mps(_numeric(raw, ["gps speed", "speed gps"]), unit=gnss_speed_unit)
    output = pd.DataFrame(
        {
            "elapsed_s": _elapsed_seconds(raw, smartphone=True),
            "gnss_latitude_deg": _numeric(raw, ["gps latitude", "latitude"]),
            "gnss_longitude_deg": _numeric(raw, ["gps longitude", "longitude"]),
            "gnss_speed_mps": gnss_speed,
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
    cleaned = _clean(output)
    cleaned.attrs["gnss_speed_unit"] = gnss_speed_unit
    return cleaned


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


@dataclass(frozen=True)
class _OffsetQuality:
    correlation: float | None
    median_absolute_speed_error_mps: float | None
    matched_samples: int


def _clock_alignment_quality(
    smartphone: pd.DataFrame,
    vehicle: pd.DataFrame,
    *,
    offset_s: float,
) -> _OffsetQuality:
    """Compare phone speed at t to vehicle speed at t + offset.

    This deliberately uses timestamps and interpolation, never CSV row index.
    """
    phone_time = smartphone.elapsed_s.to_numpy(dtype=float)
    phone_speed = smartphone.gnss_speed_mps.to_numpy(dtype=float)
    vehicle_time = vehicle.elapsed_s.to_numpy(dtype=float)
    vehicle_speed = vehicle.gt_speed_mps.to_numpy(dtype=float)
    vehicle_valid = np.isfinite(vehicle_time) & np.isfinite(vehicle_speed)
    if int(vehicle_valid.sum()) < 2:
        return _OffsetQuality(None, None, 0)
    query_time = phone_time + offset_s
    valid = (
        np.isfinite(phone_time)
        & np.isfinite(phone_speed)
        & (query_time >= vehicle_time[vehicle_valid].min())
        & (query_time <= vehicle_time[vehicle_valid].max())
    )
    if int(valid.sum()) < 3:
        return _OffsetQuality(None, None, int(valid.sum()))
    reference = np.interp(query_time[valid], vehicle_time[vehicle_valid], vehicle_speed[vehicle_valid])
    observed = phone_speed[valid]
    if np.std(observed) < 1e-4 or np.std(reference) < 1e-4:
        return _OffsetQuality(None, float(np.median(np.abs(observed - reference))), int(valid.sum()))
    return _OffsetQuality(
        correlation=float(np.corrcoef(observed, reference)[0, 1]),
        median_absolute_speed_error_mps=float(np.median(np.abs(observed - reference))),
        matched_samples=int(valid.sum()),
    )


def fit_phone_vehicle_clock_offset(
    smartphone: pd.DataFrame,
    vehicle: pd.DataFrame,
    *,
    search_min_s: float = -10.0,
    search_max_s: float = 10.0,
    search_step_s: float = 0.1,
    min_matched_samples: int = 100,
    min_correlation: float = 0.75,
    min_correlation_improvement: float = 0.015,
) -> ClockOffsetEstimate:
    """Fit and validate phone-to-vehicle offset from GNSS-speed traces.

    The output convention is explicit: vehicle time equals phone time plus the
    returned offset. A fit is rejected if the speed signal is too weak, or if
    a non-zero candidate does not materially beat the zero-offset alignment.
    """
    if search_step_s <= 0 or search_max_s < search_min_s:
        raise ValueError("clock-offset search bounds and step are invalid")
    if min_matched_samples < 3 or not -1.0 <= min_correlation <= 1.0:
        raise ValueError("clock-offset validation thresholds are invalid")
    required_phone = {"elapsed_s", "gnss_speed_mps"}
    required_vehicle = {"elapsed_s", "gt_speed_mps"}
    if missing := required_phone.difference(smartphone.columns):
        raise IOVNBDFormatError(f"phone clock fitting requires: {sorted(missing)}")
    if missing := required_vehicle.difference(vehicle.columns):
        raise IOVNBDFormatError(f"vehicle clock fitting requires: {sorted(missing)}")

    offsets = np.arange(search_min_s, search_max_s + search_step_s * 0.5, search_step_s)
    candidates: list[tuple[float, _OffsetQuality]] = []
    for raw_offset in offsets:
        offset = float(np.round(raw_offset, 8))
        quality = _clock_alignment_quality(smartphone, vehicle, offset_s=offset)
        if quality.matched_samples >= min_matched_samples and quality.correlation is not None:
            candidates.append((offset, quality))
    if not candidates:
        raise IOVNBDFormatError(
            "clock alignment failed: phone/vehicle GNSS-speed traces have no sufficiently variable overlap"
        )
    best_offset, best = max(
        candidates,
        key=lambda item: (
            float(item[1].correlation),
            -float(item[1].median_absolute_speed_error_mps or np.inf),
            -abs(item[0]),
        ),
    )
    zero = _clock_alignment_quality(smartphone, vehicle, offset_s=0.0)
    improvement = None if zero.correlation is None else float(best.correlation - zero.correlation)
    aligned_at_zero = abs(best_offset) <= search_step_s + 1e-9
    if best.correlation is None or best.correlation < min_correlation:
        raise IOVNBDFormatError(
            f"clock alignment failed validation: best speed correlation {best.correlation!r} < {min_correlation:.2f}"
        )
    if not aligned_at_zero and improvement is not None and improvement < min_correlation_improvement:
        raise IOVNBDFormatError(
            "clock alignment failed validation: best non-zero offset does not materially improve over zero offset "
            f"({improvement:.4f} < {min_correlation_improvement:.4f})"
        )
    return ClockOffsetEstimate(
        offset_s=best_offset,
        method="gnss_speed_cross_correlation",
        correlation=best.correlation,
        zero_offset_correlation=zero.correlation,
        correlation_improvement=improvement,
        median_absolute_speed_error_mps=best.median_absolute_speed_error_mps,
        matched_samples=best.matched_samples,
        candidate_offsets_tested=len(candidates),
        validation_passed=True,
    )


def _explicit_clock_offset_quality(
    smartphone: pd.DataFrame,
    vehicle: pd.DataFrame,
    *,
    offset_s: float,
) -> ClockOffsetEstimate:
    quality = _clock_alignment_quality(smartphone, vehicle, offset_s=offset_s)
    zero = _clock_alignment_quality(smartphone, vehicle, offset_s=0.0)
    improvement = (
        None
        if quality.correlation is None or zero.correlation is None
        else float(quality.correlation - zero.correlation)
    )
    return ClockOffsetEstimate(
        offset_s=float(offset_s),
        method="explicit_offset",
        correlation=quality.correlation,
        zero_offset_correlation=zero.correlation,
        correlation_improvement=improvement,
        median_absolute_speed_error_mps=quality.median_absolute_speed_error_mps,
        matched_samples=quality.matched_samples,
        candidate_offsets_tested=1,
        validation_passed=quality.correlation is not None,
    )


def _interpolate_at(
    frame: pd.DataFrame,
    grid_s: np.ndarray,
    *,
    max_interpolation_gap_s: float | None,
) -> pd.DataFrame:
    if max_interpolation_gap_s is not None and max_interpolation_gap_s <= 0:
        raise ValueError("max_interpolation_gap_s must be positive when supplied")
    result: dict[str, np.ndarray] = {"elapsed_s": grid_s}
    source_t = frame.elapsed_s.to_numpy(dtype=float)
    for column in frame.columns:
        if column == "elapsed_s":
            continue
        source_v = frame[column].to_numpy(dtype=float)
        valid = np.isfinite(source_t) & np.isfinite(source_v)
        if int(valid.sum()) < 2:
            result[column] = np.full(grid_s.shape, np.nan)
            continue
        valid_t, valid_v = source_t[valid], source_v[valid]
        interpolated = np.interp(grid_s, valid_t, valid_v)
        if max_interpolation_gap_s is None:
            result[column] = interpolated
            continue
        right = np.searchsorted(valid_t, grid_s, side="left")
        clipped_right = np.minimum(right, len(valid_t) - 1)
        exact = (right < len(valid_t)) & np.isclose(valid_t[clipped_right], grid_s, atol=1e-6)
        left = right - 1
        has_neighbours = (left >= 0) & (right < len(valid_t))
        bridge_is_short = np.zeros(grid_s.shape, dtype=bool)
        bridge_is_short[has_neighbours] = (
            valid_t[right[has_neighbours]] - valid_t[left[has_neighbours]] <= max_interpolation_gap_s
        )
        result[column] = np.where(exact | bridge_is_short, interpolated, np.nan)
    return pd.DataFrame(result)


def synchronize_streams(
    smartphone: pd.DataFrame,
    vehicle: pd.DataFrame,
    *,
    target_rate_hz: float = 10.0,
    clock_offset_s: float | None = None,
    offset_search_min_s: float = -10.0,
    offset_search_max_s: float = 10.0,
    offset_search_step_s: float = 0.1,
    max_interpolation_gap_s: float | None = 0.25,
) -> pd.DataFrame:
    """Timestamp-align a pair, then resample it to a fixed rate.

    IO-VNBD phone data are documented at 10 Hz. The default preserves measured
    samples rather than fabricating 100 Hz signal detail; runtime adapters will
    aggregate high-rate mobile/edge IMU input to the model rate later on. When
    no offset is supplied, a speed-correlation fit is required and its evidence
    is attached to the returned frame. ``clock_offset_s`` is an explicit,
    auditable override using ``vehicle_time = phone_time + offset``.
    """
    if target_rate_hz <= 0:
        raise ValueError("target_rate_hz must be positive")
    if smartphone.empty or vehicle.empty:
        raise IOVNBDFormatError("cannot synchronize an empty recording")
    alignment = (
        fit_phone_vehicle_clock_offset(
            smartphone,
            vehicle,
            search_min_s=offset_search_min_s,
            search_max_s=offset_search_max_s,
            search_step_s=offset_search_step_s,
        )
        if clock_offset_s is None
        else _explicit_clock_offset_quality(smartphone, vehicle, offset_s=clock_offset_s)
    )
    phone_time = smartphone.elapsed_s.to_numpy(dtype=float)
    vehicle_time = vehicle.elapsed_s.to_numpy(dtype=float)
    overlap_start_s = max(float(np.nanmin(phone_time)), float(np.nanmin(vehicle_time) - alignment.offset_s))
    overlap_end_s = min(float(np.nanmax(phone_time)), float(np.nanmax(vehicle_time) - alignment.offset_s))
    if overlap_end_s <= overlap_start_s:
        raise IOVNBDFormatError("paired recording has no positive overlap")
    grid_s = np.arange(overlap_start_s, overlap_end_s, 1.0 / target_rate_hz)
    if len(grid_s) < 2:
        raise IOVNBDFormatError("paired recording overlap is shorter than two target-rate samples")
    sensor_grid = _interpolate_at(smartphone, grid_s, max_interpolation_gap_s=max_interpolation_gap_s)
    vehicle_grid = _interpolate_at(vehicle, grid_s + alignment.offset_s, max_interpolation_gap_s=max_interpolation_gap_s).drop(columns="elapsed_s")
    output = pd.concat([sensor_grid, vehicle_grid], axis=1)
    output.insert(0, "timestamp_s", output.pop("elapsed_s"))
    output["gt_heading_rad"] = np.deg2rad(output.pop("gt_heading_deg"))
    output.attrs["sample_rate_hz"] = target_rate_hz
    output.attrs["max_interpolation_gap_s"] = max_interpolation_gap_s
    output.attrs["clock_alignment"] = alignment.as_dict()
    output.attrs["smartphone_gnss_speed_unit"] = smartphone.attrs.get("gnss_speed_unit", "unknown")
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
    feature_columns: list[str] | None = None,
) -> WindowedDataset:
    """Build [window, time, 9-IMU-channel] tensors and labels at window end."""
    rate = sample_rate_hz or float(synchronized.attrs.get("sample_rate_hz", 10.0))
    window_samples, stride_samples = round(window_seconds * rate), round(stride_seconds * rate)
    if window_samples < 2 or stride_samples < 1:
        raise ValueError("window and stride must yield positive sample counts")
    channels = feature_columns or [
        "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
        "gyro_x_rps", "gyro_y_rps", "gyro_z_rps",
        "mag_x_ut", "mag_y_ut", "mag_z_ut",
    ]
    if len(channels) != 9:
        raise IOVNBDFormatError("velocity windows require exactly nine input channels")
    labels = ["gt_speed_mps", "gt_heading_rad", "gt_latitude_deg", "gt_longitude_deg"]
    missing = set(channels + labels + ["timestamp_s"]).difference(synchronized.columns)
    if missing:
        raise IOVNBDFormatError(f"synchronized stream missing: {sorted(missing)}")
    frame = synchronized.reset_index(drop=True)
    timestamp = frame.timestamp_s.to_numpy(dtype=float)
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    times: list[float] = []
    max_step_s = 1.5 / rate
    for start in range(0, len(frame) - window_samples + 1, stride_samples):
        stop = start + window_samples
        window = frame.iloc[start:stop]
        numeric_window = window[channels + labels].to_numpy(dtype=float)
        if not np.isfinite(numeric_window).all():
            continue
        steps = np.diff(timestamp[start:stop])
        if np.any(steps <= 0) or np.any(steps > max_step_s):
            continue
        xs.append(window[channels].to_numpy(dtype=np.float32))
        ys.append(window.iloc[-1][labels].to_numpy(dtype=np.float32))
        times.append(float(window.timestamp_s.iloc[-1]))
    if not xs:
        raise IOVNBDFormatError(f"no complete {window_seconds:.1f}s windows at {rate:g} Hz")
    return WindowedDataset(np.stack(xs), np.stack(ys), np.asarray(times), rate)


def load_synchronized_pair(
    smartphone_path: str | Path,
    vehicle_path: str | Path,
    *,
    nrows: int | None = None,
    target_rate_hz: float = 10.0,
    smartphone_gnss_speed_unit: str = "mps",
    clock_offset_s: float | None = None,
    offset_search_min_s: float = -10.0,
    offset_search_max_s: float = 10.0,
    offset_search_step_s: float = 0.1,
    max_interpolation_gap_s: float | None = 0.25,
) -> pd.DataFrame:
    return synchronize_streams(
        load_smartphone_csv(
            smartphone_path,
            nrows=nrows,
            gnss_speed_unit=smartphone_gnss_speed_unit,
        ),
        load_vehicle_csv(vehicle_path, nrows=nrows),
        target_rate_hz=target_rate_hz,
        clock_offset_s=clock_offset_s,
        offset_search_min_s=offset_search_min_s,
        offset_search_max_s=offset_search_max_s,
        offset_search_step_s=offset_search_step_s,
        max_interpolation_gap_s=max_interpolation_gap_s,
    )
