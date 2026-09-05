"""Build a labelled non-navigation-motion dataset from MERIDIAN JSONL logs.

This intentionally does not retrain the velocity model. IO-VNBD has no
credible labels for handheld shake, mount movement, or potholes. Collect real
labelled sessions first; then use this output to train and validate a separate
motion-suitability head on held-out phones, mounts, vehicles, and roads.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np


LABELS = {
    "drive": 0,
    "parked_idle": 1,
    "pothole_bump": 2,
    "handheld_shake": 3,
    "mount_shift": 4,
}
NEGATIVE_LABELS = tuple(label for label in LABELS if label != "drive")
CHANNELS = (
    "accelerometer_x",
    "accelerometer_y",
    "accelerometer_z",
    "gyroscope_x",
    "gyroscope_y",
    "gyroscope_z",
    "magnetometer_x",
    "magnetometer_y",
    "magnetometer_z",
)


def _finite(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if np.isfinite(result) else None


def _axis(record: dict[str, Any], key: str) -> tuple[float, float, float] | None:
    value = record.get("motion", {}).get(key)
    if not isinstance(value, dict):
        return None
    components = tuple(_finite(value.get(axis)) for axis in ("x", "y", "z"))
    if any(component is None for component in components):
        return None
    return components  # type: ignore[return-value]


def load_labeled_samples(paths: list[Path]) -> dict[str, list[tuple[float, np.ndarray]]]:
    result: dict[str, list[tuple[float, np.ndarray]]] = {label: [] for label in LABELS}
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("record_type") != "sample":
                    continue
                label = str(record.get("label", "drive"))
                if label not in LABELS:
                    continue
                timestamp = _finite(record.get("timestamp_s"))
                acceleration = _axis(record, "accelerometer_mps2")
                gyroscope = _axis(record, "gyroscope_rps")
                magnetometer = _axis(record, "magnetometer_ut")
                if timestamp is None or None in (acceleration, gyroscope, magnetometer):
                    continue
                result[label].append(
                    (timestamp, np.asarray((*acceleration, *gyroscope, *magnetometer), dtype=np.float32))
                )
    for readings in result.values():
        readings.sort(key=lambda item: item[0])
    return result


def fixed_windows(
    readings: list[tuple[float, np.ndarray]],
    *,
    window_samples: int,
    stride_samples: int,
    max_gap_s: float,
) -> list[np.ndarray]:
    windows: list[np.ndarray] = []
    for start in range(0, max(0, len(readings) - window_samples + 1), stride_samples):
        selected = readings[start : start + window_samples]
        timestamps = np.asarray([item[0] for item in selected])
        if np.any(np.diff(timestamps) <= 0) or np.any(np.diff(timestamps) > max_gap_s):
            continue
        windows.append(np.stack([item[1] for item in selected]))
    return windows


def build_dataset(
    readings_by_label: dict[str, list[tuple[float, np.ndarray]]],
    *,
    window_samples: int = 20,
    stride_samples: int = 10,
    max_gap_s: float = 0.25,
) -> tuple[np.ndarray, np.ndarray, Counter[str]]:
    windows: list[np.ndarray] = []
    targets: list[int] = []
    counts: Counter[str] = Counter()
    for label, label_id in LABELS.items():
        label_windows = fixed_windows(
            readings_by_label[label],
            window_samples=window_samples,
            stride_samples=stride_samples,
            max_gap_s=max_gap_s,
        )
        windows.extend(label_windows)
        targets.extend([label_id] * len(label_windows))
        counts[label] = len(label_windows)
    if not windows:
        return (
            np.empty((0, window_samples, len(CHANNELS)), dtype=np.float32),
            np.empty((0,), dtype=np.int64),
            counts,
        )
    return np.stack(windows), np.asarray(targets, dtype=np.int64), counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path, help="Developer Mode JSONL drive logs")
    parser.add_argument("--output", type=Path, required=True, help="output .npz dataset")
    parser.add_argument("--window-samples", type=int, default=20)
    parser.add_argument("--stride-samples", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.window_samples < 2 or args.stride_samples < 1:
        raise SystemExit("window/stride sizes must be positive and window must contain at least two samples")
    readings = load_labeled_samples(args.logs)
    values, targets, counts = build_dataset(
        readings,
        window_samples=args.window_samples,
        stride_samples=args.stride_samples,
    )
    missing = [label for label in NEGATIVE_LABELS if counts[label] == 0]
    if missing:
        raise SystemExit(
            "Missing required real negative classes: "
            + ", ".join(missing)
            + ". Do not train a robustness claim without them."
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.output,
        windows=values,
        labels=targets,
        label_names=np.asarray(tuple(LABELS), dtype=str),
        channels=np.asarray(CHANNELS, dtype=str),
    )
    manifest = args.output.with_suffix(".json")
    manifest.write_text(
        json.dumps(
            {
                "source_logs": [str(path) for path in args.logs],
                "counts": dict(counts),
                "window_shape": list(values.shape),
                "labels": LABELS,
                "warning": "Dataset prepared only; no model was trained by this command.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved {values.shape[0]} labelled windows to {args.output}")
    print(json.dumps(dict(counts), indent=2))


if __name__ == "__main__":
    main()
