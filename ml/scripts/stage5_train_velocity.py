"""Run Stage 5: train a tiny 1-D CNN for forward speed estimation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from idr_ml.calibration import CalibrationResult
from idr_ml.iovnbd import build_fixed_windows, load_synchronized_pair
from idr_ml.preprocessing import VELOCITY_FEATURE_COLUMNS, calibrated_velocity_features
from idr_ml.velocity_model import file_provenance, save_velocity_artifact, train_velocity_cnn


def _clock_offset(provenance: dict[str, object]) -> float | None:
    alignment = provenance.get("clock_alignment")
    if not isinstance(alignment, dict):
        return None
    value = alignment.get("vehicle_time_s_equals_phone_time_s_plus_offset_s")
    return float(value) if isinstance(value, (int, float)) else None


def _verify_calibration_provenance(frame: object, provenance: dict[str, object]) -> None:
    """Reject calibration fitted with unknown/different timestamp alignment."""
    calibration_offset = _clock_offset(provenance)
    current_alignment = getattr(frame, "attrs", {}).get("clock_alignment")
    current_offset = _clock_offset({"clock_alignment": current_alignment})
    if calibration_offset is None:
        raise SystemExit(
            "calibration lacks timestamp-alignment provenance; rerun Stage 3 before training"
        )
    if current_offset is None or abs(calibration_offset - current_offset) > 0.11:
        raise SystemExit(
            "calibration clock offset does not match the training replay; rerun Stage 3 with the same sources"
        )
    calibration_end = provenance.get("calibration_time_end_exclusive_s")
    if not isinstance(calibration_end, (int, float)):
        raise SystemExit(
            "calibration lacks an exclusive time boundary; rerun Stage 3 with --calibration-end-seconds"
        )

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smartphone", type=Path, required=True)
    parser.add_argument("--vehicle", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, default=Path("ml/reports/stage3/calibration.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("ml/reports/stage5"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("ml/artifacts/velocity_cnn"))
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--smartphone-gnss-speed-unit", choices=("mps", "kmh"), default="mps")
    parser.add_argument("--clock-offset-s", type=float, default=None)
    args = parser.parse_args()
    frame = load_synchronized_pair(
        args.smartphone, args.vehicle, nrows=args.max_rows,
        smartphone_gnss_speed_unit=args.smartphone_gnss_speed_unit,
        clock_offset_s=args.clock_offset_s,
    )
    calibration_data = json.loads(args.calibration.read_text(encoding="utf-8"))
    calibration_data.pop("stage", None)
    calibration_provenance = calibration_data.pop("input_provenance", {})
    if not isinstance(calibration_provenance, dict):
        raise SystemExit("calibration input_provenance must be a JSON object")
    calibration = CalibrationResult(**calibration_data)
    _verify_calibration_provenance(frame, calibration_provenance)
    windows = build_fixed_windows(
        calibrated_velocity_features(frame, calibration),
        feature_columns=VELOCITY_FEATURE_COLUMNS,
    )
    model, normalization, metrics = train_velocity_cnn(windows, frame, calibration, epochs=args.epochs)
    if metrics.test_mae_mps >= metrics.classical_speed_mae_mps:
        raise SystemExit("learned velocity model did not beat the classical implied speed baseline")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "stage": 5,
        "input_provenance": {
            "clock_alignment": frame.attrs["clock_alignment"],
            "smartphone_gnss_speed_unit": frame.attrs.get("smartphone_gnss_speed_unit"),
            "clock_offset_override_s": args.clock_offset_s,
            "max_rows": args.max_rows,
            "max_interpolation_gap_s": frame.attrs.get("max_interpolation_gap_s"),
            "smartphone_source": file_provenance(args.smartphone),
            "vehicle_source": file_provenance(args.vehicle),
            "calibration_artifact": file_provenance(args.calibration),
            "calibration": calibration_provenance,
            "calibration_alignment_verified": True,
        },
        **metrics.as_dict(),
    }
    save_velocity_artifact(model, normalization, args.artifact_dir)
    report["trained_artifacts"] = {
        "model": file_provenance(args.artifact_dir / "velocity_cnn.pt"),
        "normalization": file_provenance(args.artifact_dir / "normalization.npz"),
    }
    (args.output_dir / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
