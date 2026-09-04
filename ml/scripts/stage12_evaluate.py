"""Generate the reproducible IO-VNBD masked-GNSS evaluation deliverable."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from idr_ml.calibration import CalibrationResult
from idr_ml.dead_reckoning import (
    classical_nhc_dead_reckoning,
    latlon_to_enu,
    learned_velocity_nhc_dead_reckoning,
    measure_drift,
    select_blackout,
)
from idr_ml.fusion import adaptive_fused_replay, estimate_adaptive_residual
from idr_ml.iovnbd import load_synchronized_pair
from idr_ml.plotting import write_trajectory_svg
from idr_ml.velocity_model import load_velocity_artifact


def _read_calibration(path: Path) -> CalibrationResult:
    values = json.loads(path.read_text(encoding="utf-8"))
    values.pop("stage", None)
    return CalibrationResult(**values)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smartphone", type=Path, default=Path("ml/data/raw/iovnbd_m/S-M.csv"))
    parser.add_argument("--vehicle", type=Path, default=Path("ml/data/raw/iovnbd_m/V-M.csv"))
    parser.add_argument("--calibration", type=Path, default=Path("ml/reports/stage3/calibration.json"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("ml/artifacts/velocity_cnn"))
    parser.add_argument("--output-dir", type=Path, default=Path("docs/evaluation/stage12"))
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--start-seconds", type=float, default=430.0)
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--history-seconds", type=float, default=60.0)
    args = parser.parse_args()

    frame = load_synchronized_pair(args.smartphone, args.vehicle, nrows=args.max_rows)
    calibration = _read_calibration(args.calibration)
    history = select_blackout(
        frame, start_seconds=args.start_seconds - args.history_seconds, duration_seconds=args.history_seconds
    )
    blackout = select_blackout(frame, start_seconds=args.start_seconds, duration_seconds=args.duration_seconds)
    model, normalization = load_velocity_artifact(args.artifact_dir)
    residual = estimate_adaptive_residual(history, calibration, model, normalization)

    classical = classical_nhc_dead_reckoning(blackout, calibration)
    learned = learned_velocity_nhc_dead_reckoning(blackout, calibration, model, normalization)
    fused = adaptive_fused_replay(blackout, calibration, model, normalization, residual)
    measurements = {
        "classical_nhc": measure_drift(blackout, classical),
        "learned_speed_nhc": measure_drift(blackout, learned),
        "masked_gnss_adaptive_fusion": measure_drift(blackout, fused),
    }
    fusion_metrics = measurements["masked_gnss_adaptive_fusion"]
    if fusion_metrics.drift_percent >= 10.0:
        raise SystemExit(f"fusion drift target not met: {fusion_metrics.drift_percent:.2f}% >= 10%")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    gt_east, gt_north = latlon_to_enu(
        blackout.gt_latitude_deg.to_numpy(float), blackout.gt_longitude_deg.to_numpy(float)
    )
    position_plot = write_trajectory_svg(
        [
            ("Ground truth", gt_east, gt_north, "#35d07f"),
            ("Learned-speed NHC", learned.east_m, learned.north_m, "#f5b942"),
            ("Masked-GNSS adaptive fusion", fused.east_m, fused.north_m, "#2997ff"),
        ],
        args.output_dir / "position_plot.svg",
        title="MERIDIAN Stage 12 — IO-VNBD position inference",
        subtitle=(
            f"Driver B held-out {args.duration_seconds:.0f} s GNSS blackout at {fusion_metrics.update_rate_hz:.1f} Hz; "
            f"fusion endpoint drift {fusion_metrics.end_position_error_m:.2f} m ({fusion_metrics.drift_percent:.2f}%)"
        ),
    )
    trajectory = pd.DataFrame(
        {
            "timestamp_s": blackout.timestamp_s,
            "ground_truth_east_m": gt_east,
            "ground_truth_north_m": gt_north,
            "learned_nhc_east_m": learned.east_m,
            "learned_nhc_north_m": learned.north_m,
            "fused_east_m": fused.east_m,
            "fused_north_m": fused.north_m,
        }
    )
    trajectory.to_csv(args.output_dir / "trajectory.csv", index=False)
    metric_rows = [
        {"method": method, **metrics.as_dict()} for method, metrics in measurements.items()
    ]
    pd.DataFrame(metric_rows).to_csv(args.output_dir / "metrics.csv", index=False, float_format="%.6f")
    report = {
        "stage": 12,
        "dataset": "IO-VNBD M (Driver B)",
        "split": "held-out timestamp interval",
        "gnss_blackout": {"start_seconds": args.start_seconds, "duration_seconds": args.duration_seconds},
        "pre_outage_history_seconds": args.history_seconds,
        "sample_rate_hz": fusion_metrics.update_rate_hz,
        "residual": residual.as_dict(),
        "acceptance": {
            "max_drift_percent": 10.0,
            "observed_drift_percent": fusion_metrics.drift_percent,
            "passes": fusion_metrics.drift_percent < 10.0,
        },
        "metrics": {method: metrics.as_dict() for method, metrics in measurements.items()},
        "artifacts": {
            "position_plot": position_plot.name,
            "metrics_table": "metrics.csv",
            "trajectory": "trajectory.csv",
        },
    }
    (args.output_dir / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
