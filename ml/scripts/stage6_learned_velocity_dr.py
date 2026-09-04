"""Run Stage 6: replace classical speed integration with the trained CNN."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from idr_ml.calibration import CalibrationResult
from idr_ml.dead_reckoning import learned_velocity_nhc_dead_reckoning, measure_drift, select_blackout
from idr_ml.iovnbd import load_synchronized_pair
from idr_ml.velocity_model import load_velocity_artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smartphone", type=Path, required=True)
    parser.add_argument("--vehicle", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, default=Path("ml/reports/stage3/calibration.json"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("ml/artifacts/velocity_cnn"))
    parser.add_argument("--output-dir", type=Path, default=Path("ml/reports/stage6"))
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--start-seconds", type=float, default=430.0)
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    args = parser.parse_args()
    frame = load_synchronized_pair(args.smartphone, args.vehicle, nrows=args.max_rows)
    calibration_data = json.loads(args.calibration.read_text(encoding="utf-8"))
    calibration_data.pop("stage", None)
    calibration = CalibrationResult(**calibration_data)
    blackout = select_blackout(frame, start_seconds=args.start_seconds, duration_seconds=args.duration_seconds)
    model, normalization = load_velocity_artifact(args.artifact_dir)
    result = learned_velocity_nhc_dead_reckoning(blackout, calibration, model, normalization)
    metrics = measure_drift(blackout, result)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result.as_frame().to_csv(args.output_dir / "trajectory.csv", index=False)
    report = {"stage": 6, "blackout_start_s": args.start_seconds, "blackout_duration_s": args.duration_seconds, **metrics.as_dict()}
    (args.output_dir / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
