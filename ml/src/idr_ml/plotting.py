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
