"""Score a physical MERIDIAN JSONL trip against retained phone GNSS reference.

The reference GNSS is deliberately not treated as survey-grade ground truth.
It is recorded while the manual outage simulator is on, but the mobile engine
does not consume it for DR. This script reports that distinction in every
output and is intended to be run after a fixed-mount road drive.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable


EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class DriveSample:
    timestamp_s: float
    mode: str
    predicted_latitude_deg: float | None
    predicted_longitude_deg: float | None
    reference_latitude_deg: float | None
    reference_longitude_deg: float | None
    confidence: float | None

    @property
    def has_comparison(self) -> bool:
        return all(
            value is not None
            for value in (
                self.predicted_latitude_deg,
                self.predicted_longitude_deg,
                self.reference_latitude_deg,
                self.reference_longitude_deg,
            )
        )


@dataclass(frozen=True)
class BlackoutInterval:
    start_s: float
    end_s: float
    source: str


def _finite(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _nested(record: dict[str, Any], *keys: str) -> object:
    value: object = record
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def load_trip(path: Path) -> tuple[list[DriveSample], list[dict[str, Any]]]:
    samples: list[DriveSample] = []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {error}") from error
            record_type = record.get("record_type")
            if record_type == "event":
                events.append(record)
                continue
            if record_type != "sample":
                continue
            timestamp_s = _finite(record.get("timestamp_s"))
            if timestamp_s is None:
                continue
            samples.append(
                DriveSample(
                    timestamp_s=timestamp_s,
                    mode=str(record.get("mode", "unknown")),
                    predicted_latitude_deg=_finite(
                        _nested(record, "position", "latitude_deg")
                    ),
                    predicted_longitude_deg=_finite(
                        _nested(record, "position", "longitude_deg")
                    ),
                    reference_latitude_deg=_finite(
                        _nested(record, "position", "reference_gnss", "latitude_deg")
                    ),
                    reference_longitude_deg=_finite(
                        _nested(record, "position", "reference_gnss", "longitude_deg")
                    ),
                    confidence=_finite(
                        _nested(record, "metrics", "prediction_confidence")
                    ),
                )
            )
    samples.sort(key=lambda item: item.timestamp_s)
    events.sort(key=lambda item: _finite(item.get("timestamp_s")) or -math.inf)
    return samples, events


def _manual_intervals(
    events: Iterable[dict[str, Any]], end_s: float
) -> list[BlackoutInterval]:
    result: list[BlackoutInterval] = []
    start_s: float | None = None
    for event in events:
        if event.get("event") != "gnss_aiding_changed":
            continue
        timestamp_s = _finite(event.get("timestamp_s"))
        enabled = event.get("enabled")
        if timestamp_s is None or not isinstance(enabled, bool):
            continue
        if not enabled and start_s is None:
            start_s = timestamp_s
        elif enabled and start_s is not None:
            if timestamp_s > start_s:
                result.append(BlackoutInterval(start_s, timestamp_s, "manual"))
            start_s = None
    if start_s is not None and end_s > start_s:
        result.append(BlackoutInterval(start_s, end_s, "manual_open"))
    return result


def _mode_intervals(samples: Iterable[DriveSample]) -> list[BlackoutInterval]:
    result: list[BlackoutInterval] = []
    start_s: float | None = None
    end_s: float | None = None
    for sample in samples:
        if sample.mode == "dead_reckoning":
            if start_s is None:
                start_s = sample.timestamp_s
            end_s = sample.timestamp_s
        elif start_s is not None and end_s is not None:
            result.append(BlackoutInterval(start_s, end_s, "observed_mode"))
            start_s = None
            end_s = None
    if start_s is not None and end_s is not None:
        result.append(BlackoutInterval(start_s, end_s, "observed_mode"))
    return result


def blackout_intervals(
    samples: list[DriveSample], events: list[dict[str, Any]]
) -> list[BlackoutInterval]:
    if not samples:
        return []
    manual = _manual_intervals(events, samples[-1].timestamp_s)
    return manual or _mode_intervals(samples)


def haversine_m(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    d_lat = lat_b - lat_a
    d_lon = math.radians(longitude_b - longitude_a)
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _distance_travelled(samples: list[DriveSample]) -> float:
    result = 0.0
    previous: DriveSample | None = None
    for sample in samples:
        if sample.reference_latitude_deg is None or sample.reference_longitude_deg is None:
            continue
        if previous is not None:
            result += haversine_m(
                previous.reference_latitude_deg,  # type: ignore[arg-type]
                previous.reference_longitude_deg,  # type: ignore[arg-type]
                sample.reference_latitude_deg,
                sample.reference_longitude_deg,
            )
        previous = sample
    return result


def _window_samples(
    samples: list[DriveSample], interval: BlackoutInterval, duration_s: float
) -> list[DriveSample]:
    end_s = min(interval.end_s, interval.start_s + duration_s)
    return [
        sample
        for sample in samples
        if interval.start_s <= sample.timestamp_s <= end_s and sample.has_comparison
    ]


def evaluate_window(
    samples: list[DriveSample], interval: BlackoutInterval, duration_s: float
) -> dict[str, Any]:
    chosen = _window_samples(samples, interval, duration_s)
    result: dict[str, Any] = {
        "requested_blackout_s": duration_s,
        "source": interval.source,
        "available_s": max(0.0, interval.end_s - interval.start_s),
        "status": "insufficient_data",
    }
    if len(chosen) < 2:
        return result
    actual_duration_s = chosen[-1].timestamp_s - chosen[0].timestamp_s
    if actual_duration_s < duration_s * 0.8:
        result["actual_duration_s"] = actual_duration_s
        return result
    start = chosen[0]
    end = chosen[-1]
    endpoint_error_m = haversine_m(
        end.predicted_latitude_deg,  # type: ignore[arg-type]
        end.predicted_longitude_deg,  # type: ignore[arg-type]
        end.reference_latitude_deg,  # type: ignore[arg-type]
        end.reference_longitude_deg,  # type: ignore[arg-type]
    )
    distance_m = _distance_travelled(chosen)
    confidences = [sample.confidence for sample in chosen if sample.confidence is not None]
    result.update(
        {
            "status": "ok",
            "start_timestamp_s": start.timestamp_s,
            "end_timestamp_s": end.timestamp_s,
            "actual_duration_s": actual_duration_s,
            "samples": len(chosen),
            "update_rate_hz": (len(chosen) - 1) / actual_duration_s
            if actual_duration_s > 0
            else 0.0,
            "distance_m": distance_m,
            "endpoint_error_m": endpoint_error_m,
            "drift_percent": endpoint_error_m / distance_m * 100
            if distance_m > 0.5
            else None,
            "mean_prediction_confidence": fmean(confidences) if confidences else None,
        }
    )
    return result


def transition_latency_ms(
    samples: list[DriveSample], events: list[dict[str, Any]]
) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {"loss": [], "reacquisition": []}
    for event in events:
        if event.get("event") != "gnss_aiding_changed":
            continue
        timestamp_s = _finite(event.get("timestamp_s"))
        enabled = event.get("enabled")
        if timestamp_s is None or not isinstance(enabled, bool):
            continue
        expected = (
            (lambda mode: mode == "dead_reckoning")
            if not enabled
            else (lambda mode: mode in {"reacquiring", "gnss_aided_ins"})
        )
        next_sample = next(
            (sample for sample in samples if sample.timestamp_s >= timestamp_s and expected(sample.mode)),
            None,
        )
        if next_sample is not None:
            key = "reacquisition" if enabled else "loss"
            result[key].append((next_sample.timestamp_s - timestamp_s) * 1000)
    return result


def write_outputs(
    output_dir: Path, metrics: list[dict[str, Any]], latency: dict[str, list[float]]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "reference": "retained phone GNSS reference; not survey-grade ground truth",
        "metrics": metrics,
        "transition_latency_ms": latency,
    }
    (output_dir / "live_drive_metrics.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8"
    )
    fields = sorted({key for metric in metrics for key in metric})
    with (output_dir / "live_drive_windows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(metrics)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trip_log", type=Path, help="MERIDIAN JSONL log exported by Developer Mode")
    parser.add_argument(
        "--windows",
        default="10,30,60",
        help="comma-separated requested blackout durations in seconds (default: 10,30,60)",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("ml/reports/live_drive"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    durations = [float(value) for value in args.windows.split(",") if value.strip()]
    if not durations or any(value <= 0 for value in durations):
        raise SystemExit("--windows must contain positive durations")
    samples, events = load_trip(args.trip_log)
    intervals = blackout_intervals(samples, events)
    if not intervals:
        raise SystemExit("No manual or observed dead-reckoning blackout interval found in log")
    metrics: list[dict[str, Any]] = []
    for duration_s in durations:
        interval = next(
            (item for item in intervals if item.end_s - item.start_s >= duration_s * 0.8),
            None,
        )
        if interval is None:
            metrics.append(
                {
                    "requested_blackout_s": duration_s,
                    "status": "no_interval_long_enough",
                }
            )
        else:
            metrics.append(evaluate_window(samples, interval, duration_s))
    latency = transition_latency_ms(samples, events)
    write_outputs(args.output_dir, metrics, latency)
    for metric in metrics:
        if metric["status"] == "ok":
            print(
                f"{metric['requested_blackout_s']:.0f}s: "
                f"{metric['endpoint_error_m']:.2f} m / {metric['distance_m']:.2f} m "
                f"({metric['drift_percent']:.2f}%), {metric['update_rate_hz']:.2f} Hz"
            )
        else:
            print(f"{metric['requested_blackout_s']:.0f}s: {metric['status']}")
    print(f"Saved {args.output_dir / 'live_drive_metrics.json'}")


if __name__ == "__main__":
    main()
