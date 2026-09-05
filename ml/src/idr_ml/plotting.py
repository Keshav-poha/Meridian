"""Small SVG plotting helpers with no matplotlib dependency."""

from __future__ import annotations

from html import escape
from pathlib import Path

import numpy as np
import pandas as pd


def _scale(values: np.ndarray, low: float, high: float) -> np.ndarray:
    minimum, maximum = np.nanmin(values), np.nanmax(values)
    if not np.isfinite(minimum) or not np.isfinite(maximum) or maximum - minimum < 1e-9:
        return np.full(values.shape, (low + high) / 2.0)
    return low + (values - minimum) * (high - low) / (maximum - minimum)


def _polyline(xs: np.ndarray, ys: np.ndarray) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys, strict=True))


def write_stage2_sanity_svg(frame: pd.DataFrame, output_path: str | Path) -> Path:
    """Plot reference path beside raw accelerometer magnitude over time."""
    required = {
        "timestamp_s", "gt_latitude_deg", "gt_longitude_deg",
        "accel_x_mps2", "accel_y_mps2", "accel_z_mps2",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"cannot plot missing fields: {sorted(missing)}")
    data = frame.dropna(subset=required).iloc[:: max(1, len(frame) // 1200)]
    if len(data) < 2:
        raise ValueError("sanity plot needs at least two finite synchronized samples")
    latitude = data.gt_latitude_deg.to_numpy(float)
    longitude = data.gt_longitude_deg.to_numpy(float)
    north_m = (latitude - latitude[0]) * 111_320.0
    east_m = (longitude - longitude[0]) * 111_320.0 * np.cos(np.deg2rad(latitude[0]))
    path_x, path_y = _scale(east_m, 70, 450), 480 - _scale(north_m, 70, 450)
    time_s = data.timestamp_s.to_numpy(float)
    accel = np.linalg.norm(data[["accel_x_mps2", "accel_y_mps2", "accel_z_mps2"]].to_numpy(float), axis=1)
    signal_x, signal_y = _scale(time_s, 530, 930), 480 - _scale(accel, 80, 420)
    summary = (
        f"{len(frame):,} synchronized samples | {time_s[-1] - time_s[0]:.1f} s | "
        f"reference path {np.hypot(np.diff(east_m), np.diff(north_m)).sum():.1f} m"
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="560" viewBox="0 0 1000 560">
  <rect width="1000" height="560" fill="#070A0F"/>
  <style>text{{font-family:Arial,sans-serif;fill:#dce7f4}} .muted{{fill:#8ea2b6}} .axis{{stroke:#33485d;stroke-width:1}} .title{{font-size:18px;font-weight:bold}} .label{{font-size:13px}}</style>
  <text x="40" y="38" class="title">IO-VNBD Stage 2 sanity check</text><text x="40" y="60" class="muted label">{escape(summary)}</text>
  <rect x="55" y="85" width="420" height="420" rx="10" fill="#0f1822" stroke="#33485d"/>
  <text x="70" y="112" class="title">Vehicle ground-truth path (local ENU)</text>
  <line x1="70" y1="480" x2="450" y2="480" class="axis"/><line x1="70" y1="480" x2="70" y2="130" class="axis"/>
  <polyline points="{_polyline(path_x, path_y)}" fill="none" stroke="#2997ff" stroke-width="2.5"/>
  <circle cx="{path_x[0]:.2f}" cy="{path_y[0]:.2f}" r="5" fill="#2acb86"/><circle cx="{path_x[-1]:.2f}" cy="{path_y[-1]:.2f}" r="6" fill="#f04444"/>
  <text x="75" y="500" class="muted label">east</text><text x="45" y="145" class="muted label">north</text>
  <rect x="510" y="85" width="445" height="420" rx="10" fill="#0f1822" stroke="#33485d"/>
  <text x="525" y="112" class="title">Raw accelerometer magnitude</text>
  <line x1="530" y1="480" x2="930" y2="480" class="axis"/><line x1="530" y1="480" x2="530" y2="130" class="axis"/>
  <polyline points="{_polyline(signal_x, signal_y)}" fill="none" stroke="#f5b942" stroke-width="1.5"/>
  <text x="535" y="500" class="muted label">elapsed time (s)</text><text x="535" y="135" class="muted label">|a| m/s²</text>
</svg>'''
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")
    return output


def write_trajectory_svg(
    traces: list[tuple[str, np.ndarray, np.ndarray, str]],
    output_path: str | Path,
    *,
    title: str,
    subtitle: str,
) -> Path:
    """Write a self-contained, consistently scaled local-ENU trajectory plot.

    An SVG keeps the evaluation deliverable portable and reproducible without a
    desktop plotting dependency. Every trace uses the same scale, so endpoint
    drift remains visually comparable to the ground-truth path.
    """
    if not traces:
        raise ValueError("trajectory plot needs at least one trace")
    prepared: list[tuple[str, np.ndarray, np.ndarray, str]] = []
    for name, east, north, color in traces:
        east_values, north_values = np.asarray(east, dtype=float), np.asarray(north, dtype=float)
        if len(east_values) < 2 or len(east_values) != len(north_values):
            raise ValueError(f"trace {name!r} must contain equally-sized east/north arrays")
        if not np.isfinite(east_values).all() or not np.isfinite(north_values).all():
            raise ValueError(f"trace {name!r} contains non-finite coordinates")
        prepared.append((name, east_values, north_values, color))

    all_east = np.concatenate([east for _, east, _, _ in prepared])
    all_north = np.concatenate([north for _, _, north, _ in prepared])
    east_low, east_high = float(np.min(all_east)), float(np.max(all_east))
    north_low, north_high = float(np.min(all_north)), float(np.max(all_north))
    span = max(east_high - east_low, north_high - north_low, 1.0)
    pad = 0.08 * span
    east_low, east_high = east_low - pad, east_high + pad
    north_low, north_high = north_low - pad, north_high + pad
    plot_left, plot_right, plot_top, plot_bottom = 90.0, 915.0, 120.0, 530.0

    def project(east: np.ndarray, north: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = plot_left + (east - east_low) * (plot_right - plot_left) / (east_high - east_low)
        y = plot_bottom - (north - north_low) * (plot_bottom - plot_top) / (north_high - north_low)
        return x, y

    trace_markup: list[str] = []
    legend_markup: list[str] = []
    for index, (name, east, north, color) in enumerate(prepared):
        x, y = project(east, north)
        trace_markup.append(
            f'<polyline points="{_polyline(x, y)}" fill="none" stroke="{escape(color)}" '
            f'stroke-width="{3.2 if index == 0 else 2.4}" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        if index == 0:
            trace_markup.append(f'<circle cx="{x[0]:.2f}" cy="{y[0]:.2f}" r="5" fill="#35d07f"/>')
        trace_markup.append(f'<circle cx="{x[-1]:.2f}" cy="{y[-1]:.2f}" r="5" fill="{escape(color)}"/>')
        legend_y = 74 + 24 * index
        legend_markup.append(
            f'<line x1="{590}" y1="{legend_y}" x2="{615}" y2="{legend_y}" stroke="{escape(color)}" stroke-width="3"/>'
            f'<text x="623" y="{legend_y + 5}" class="label">{escape(name)}</text>'
        )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600" viewBox="0 0 1000 600">
  <rect width="1000" height="600" fill="#070A0F"/>
  <style>text{{font-family:Arial,sans-serif;fill:#dce7f4}} .title{{font-size:21px;font-weight:bold}} .subtitle,.axis{{font-size:13px;fill:#8ea2b6}} .label{{font-size:13px}} .grid{{stroke:#243545;stroke-width:1}}</style>
  <text x="48" y="42" class="title">{escape(title)}</text>
  <text x="48" y="66" class="subtitle">{escape(subtitle)}</text>
  <rect x="{plot_left}" y="{plot_top}" width="{plot_right - plot_left}" height="{plot_bottom - plot_top}" rx="10" fill="#0f1822" stroke="#33485d"/>
  <line x1="{plot_left}" y1="{plot_bottom}" x2="{plot_right}" y2="{plot_bottom}" class="grid"/>
  <line x1="{plot_left}" y1="{plot_top}" x2="{plot_left}" y2="{plot_bottom}" class="grid"/>
  {''.join(trace_markup)}
  <text x="{plot_right - 40}" y="{plot_bottom + 30}" class="axis">east (m)</text>
  <text x="{plot_left - 55}" y="{plot_top + 16}" class="axis">north (m)</text>
  <text x="{plot_left}" y="{plot_bottom + 30}" class="axis">{east_low:.0f}</text>
  <text x="{plot_right - 20}" y="{plot_bottom + 30}" class="axis">{east_high:.0f}</text>
  <text x="{plot_left - 45}" y="{plot_bottom}" class="axis">{north_low:.0f}</text>
  <text x="{plot_left - 45}" y="{plot_top + 5}" class="axis">{north_high:.0f}</text>
  {''.join(legend_markup)}
</svg>'''
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")
    return output


def _plot_series(values: np.ndarray, *, maximum_points: int = 1200) -> np.ndarray:
    """Keep SVG evidence compact while retaining its first and final sample."""

    if len(values) <= maximum_points:
        return values
    indices = np.unique(np.linspace(0, len(values) - 1, maximum_points, dtype=int))
    return values[indices]


def _validate_equal_finite_series(**series: np.ndarray) -> int:
    lengths = {len(values) for values in series.values()}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) < 2:
        raise ValueError("plot series must have the same length and at least two samples")
    for name, values in series.items():
        if not np.isfinite(values).all():
            raise ValueError(f"plot series {name!r} contains non-finite values")
    return next(iter(lengths))


def write_speed_tracking_svg(
    timestamp_s: np.ndarray,
    ground_truth_speed_mps: np.ndarray,
    estimated_speed_mps: np.ndarray,
    output_path: str | Path,
    *,
    title: str,
    subtitle: str,
) -> Path:
    """Write a time-series speed comparison for the held-out blackout."""

    timestamp = np.asarray(timestamp_s, dtype=float)
    ground_truth = np.asarray(ground_truth_speed_mps, dtype=float)
    estimated = np.asarray(estimated_speed_mps, dtype=float)
    _validate_equal_finite_series(
        timestamp_s=timestamp,
        ground_truth_speed_mps=ground_truth,
        estimated_speed_mps=estimated,
    )
    elapsed = timestamp - timestamp[0]
    if np.any(np.diff(elapsed) < 0):
        raise ValueError("speed plot timestamps must be ordered")
    indices = (
        np.arange(len(elapsed))
        if len(elapsed) <= 1200
        else np.unique(np.linspace(0, len(elapsed) - 1, 1200, dtype=int))
    )
    elapsed, ground_truth, estimated = (
        elapsed[indices],
        ground_truth[indices],
        estimated[indices],
    )
    left, right, top, bottom = 90.0, 915.0, 125.0, 525.0
    x_min, x_max = float(elapsed.min()), max(float(elapsed.max()), float(elapsed.min()) + 1.0)
    y_max = max(float(np.max(ground_truth)), float(np.max(estimated)), 1.0) * 1.12

    def x(values: np.ndarray) -> np.ndarray:
        return left + (values - x_min) * (right - left) / (x_max - x_min)

    def y(values: np.ndarray) -> np.ndarray:
        return bottom - values * (bottom - top) / y_max

    grid = "".join(
        f'<line x1="{left}" y1="{top + index * (bottom - top) / 4:.2f}" '
        f'x2="{right}" y2="{top + index * (bottom - top) / 4:.2f}" class="grid"/>'
        for index in range(5)
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600" viewBox="0 0 1000 600">
  <rect width="1000" height="600" fill="#070A0F"/>
  <style>text{{font-family:Arial,sans-serif;fill:#dce7f4}} .title{{font-size:21px;font-weight:bold}} .subtitle,.axis{{font-size:13px;fill:#8ea2b6}} .label{{font-size:13px}} .grid{{stroke:#243545;stroke-width:1}}</style>
  <text x="48" y="42" class="title">{escape(title)}</text>
  <text x="48" y="66" class="subtitle">{escape(subtitle)}</text>
  <rect x="{left}" y="{top}" width="{right - left}" height="{bottom - top}" rx="10" fill="#0f1822" stroke="#33485d"/>
  {grid}
  <polyline points="{_polyline(x(elapsed), y(ground_truth))}" fill="none" stroke="#35d07f" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/>
  <polyline points="{_polyline(x(elapsed), y(estimated))}" fill="none" stroke="#2997ff" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>
  <line x1="610" y1="93" x2="635" y2="93" stroke="#35d07f" stroke-width="3"/><text x="643" y="98" class="label">Ground-truth speed</text>
  <line x1="610" y1="116" x2="635" y2="116" stroke="#2997ff" stroke-width="3"/><text x="643" y="121" class="label">AI-assisted INS speed</text>
  <text x="{left}" y="{bottom + 30}" class="axis">{x_min:.0f}</text><text x="{right - 22}" y="{bottom + 30}" class="axis">{x_max:.0f}</text>
  <text x="{left - 48}" y="{bottom}" class="axis">0</text><text x="{left - 58}" y="{top + 5}" class="axis">{y_max:.1f}</text>
  <text x="{right - 80}" y="{bottom + 30}" class="axis">time (s)</text><text x="{left - 66}" y="{top + 16}" class="axis">speed (m/s)</text>
</svg>'''
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")
    return output


def write_drift_vs_distance_svg(
    distance_m: np.ndarray,
    drift_error_m: np.ndarray,
    output_path: str | Path,
    *,
    title: str,
    subtitle: str,
    ceiling_fraction: float = 0.10,
) -> Path:
    """Write cumulative endpoint error against travelled-distance evidence."""

    distance = np.asarray(distance_m, dtype=float)
    error = np.asarray(drift_error_m, dtype=float)
    _validate_equal_finite_series(distance_m=distance, drift_error_m=error)
    if ceiling_fraction <= 0 or np.any(distance < 0) or np.any(np.diff(distance) < -1e-6):
        raise ValueError("drift plot needs non-negative cumulative distance and a positive ceiling")
    indices = (
        np.arange(len(distance))
        if len(distance) <= 1200
        else np.unique(np.linspace(0, len(distance) - 1, 1200, dtype=int))
    )
    distance, error = distance[indices], error[indices]
    ceiling = distance * ceiling_fraction
    left, right, top, bottom = 90.0, 915.0, 125.0, 525.0
    x_max = max(float(distance.max()), 1.0)
    y_max = max(float(error.max()), float(ceiling.max()), 1.0) * 1.12

    def x(values: np.ndarray) -> np.ndarray:
        return left + values * (right - left) / x_max

    def y(values: np.ndarray) -> np.ndarray:
        return bottom - values * (bottom - top) / y_max

    grid = "".join(
        f'<line x1="{left}" y1="{top + index * (bottom - top) / 4:.2f}" '
        f'x2="{right}" y2="{top + index * (bottom - top) / 4:.2f}" class="grid"/>'
        for index in range(5)
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600" viewBox="0 0 1000 600">
  <rect width="1000" height="600" fill="#070A0F"/>
  <style>text{{font-family:Arial,sans-serif;fill:#dce7f4}} .title{{font-size:21px;font-weight:bold}} .subtitle,.axis{{font-size:13px;fill:#8ea2b6}} .label{{font-size:13px}} .grid{{stroke:#243545;stroke-width:1}}</style>
  <text x="48" y="42" class="title">{escape(title)}</text>
  <text x="48" y="66" class="subtitle">{escape(subtitle)}</text>
  <rect x="{left}" y="{top}" width="{right - left}" height="{bottom - top}" rx="10" fill="#0f1822" stroke="#33485d"/>
  {grid}
  <polyline points="{_polyline(x(distance), y(error))}" fill="none" stroke="#2997ff" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/>
  <polyline points="{_polyline(x(distance), y(ceiling))}" fill="none" stroke="#f04444" stroke-width="2.2" stroke-dasharray="9 7" stroke-linejoin="round"/>
  <line x1="610" y1="93" x2="635" y2="93" stroke="#2997ff" stroke-width="3"/><text x="643" y="98" class="label">Cumulative drift error</text>
  <line x1="610" y1="116" x2="635" y2="116" stroke="#f04444" stroke-width="3" stroke-dasharray="7 5"/><text x="643" y="121" class="label">10% benchmark ceiling</text>
  <text x="{left}" y="{bottom + 30}" class="axis">0</text><text x="{right - 42}" y="{bottom + 30}" class="axis">{x_max:.0f}</text>
  <text x="{left - 48}" y="{bottom}" class="axis">0</text><text x="{left - 58}" y="{top + 5}" class="axis">{y_max:.1f}</text>
  <text x="{right - 150}" y="{bottom + 30}" class="axis">distance travelled (m)</text><text x="{left - 72}" y="{top + 16}" class="axis">error (m)</text>
</svg>'''
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")
    return output
