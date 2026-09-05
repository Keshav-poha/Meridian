"""Run Stage 5: train a tiny 1-D CNN for forward speed estimation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from idr_ml.calibration import CalibrationResult
from idr_ml.iovnbd import build_fixed_windows, load_synchronized_pair
from idr_ml.preprocessing import VELOCITY_FEATURE_COLUMNS, calibrated_velocity_features
from idr_ml.velocity_model import save_velocity_artifact, train_velocity_cnn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smartphone", type=Path, required=True)
    parser.add_argument("--vehicle", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, default=Path("ml/reports/stage3/calibration.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("ml/reports/stage5"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("ml/artifacts/velocity_cnn"))
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--epochs", type=int, default=40)
    args = parser.parse_args()
    frame = load_synchronized_pair(args.smartphone, args.vehicle, nrows=args.max_rows)
    calibration_data = json.loads(args.calibration.read_text(encoding="utf-8"))
    calibration_data.pop("stage", None)
    calibration = CalibrationResult(**calibration_data)
    windows = build_fixed_windows(
        calibrated_velocity_features(frame, calibration),
        feature_columns=VELOCITY_FEATURE_COLUMNS,
    )
    model, normalization, metrics = train_velocity_cnn(windows, frame, calibration, epochs=args.epochs)
    if metrics.test_mae_mps >= metrics.classical_speed_mae_mps:
        raise SystemExit("learned velocity model did not beat the classical implied speed baseline")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "metrics.json").write_text(json.dumps({"stage": 5, **metrics.as_dict()}, indent=2), encoding="utf-8")
    save_velocity_artifact(model, normalization, args.artifact_dir)
    print(json.dumps({"stage": 5, **metrics.as_dict()}))


if __name__ == "__main__":
    main()
