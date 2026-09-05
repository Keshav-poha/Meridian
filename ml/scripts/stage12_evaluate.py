"""Generate the reproducible IO-VNBD masked-GNSS evaluation deliverable."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from idr_ml.calibration import CalibrationResult
from idr_ml.dead_reckoning import (
    classical_nhc_dead_reckoning,
    initial_state_from_preoutage_reference,
    latlon_to_enu,
    learned_velocity_nhc_dead_reckoning,
    measure_drift,
    select_blackout,
)
from idr_ml.fusion import adaptive_fused_replay, estimate_adaptive_residual
from idr_ml.iovnbd import load_synchronized_pair
from idr_ml.plotting import write_trajectory_svg
from idr_ml.velocity_model import (
    file_provenance,
    load_portable_onnx_velocity_artifact,
)


def _read_calibration(path: Path) -> tuple[CalibrationResult, dict[str, object]]:
    values = json.loads(path.read_text(encoding="utf-8"))
    values.pop("stage", None)
    provenance = values.pop("input_provenance", {})
    return CalibrationResult(**values), provenance


def _clock_offset(provenance: dict[str, object]) -> float | None:
    alignment = provenance.get("clock_alignment")
    if not isinstance(alignment, dict):
        return None
    value = alignment.get("vehicle_time_s_equals_phone_time_s_plus_offset_s")
    return float(value) if isinstance(value, (int, float)) else None


def _calibration_end_exclusive_s(provenance: dict[str, object]) -> float | None:
    value = provenance.get("calibration_time_end_exclusive_s")
    return float(value) if isinstance(value, (int, float)) else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smartphone", type=Path, default=Path("ml/data/raw/iovnbd_m/S-M.csv"))
    parser.add_argument("--vehicle", type=Path, default=Path("ml/data/raw/iovnbd_m/V-M.csv"))
    parser.add_argument("--calibration", type=Path, default=Path("ml/reports/stage3/calibration.json"))
    parser.add_argument("--model", type=Path, default=Path("shared/models/velocity_cnn.onnx"))
    parser.add_argument("--normalization", type=Path, default=Path("shared/models/velocity_cnn.normalization.json"))
    parser.add_argument("--model-manifest", type=Path, default=Path("shared/models/velocity_cnn.onnx.manifest.json"))
    parser.add_argument("--smartphone-gnss-speed-unit", choices=("mps", "kmh"), default="mps")
    parser.add_argument("--clock-offset-s", type=float, default=None)
    parser.add_argument(
        "--allow-unverified-calibration", action="store_true",
        help="allow a legacy calibration JSON without timestamp-alignment provenance",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/benchmarks/iovnbd-driver-b-stage12"),
    )
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--start-seconds", type=float, default=430.0)
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--history-seconds", type=float, default=60.0)
    args = parser.parse_args()

    frame = load_synchronized_pair(
        args.smartphone,
        args.vehicle,
        nrows=args.max_rows,
        smartphone_gnss_speed_unit=args.smartphone_gnss_speed_unit,
        clock_offset_s=args.clock_offset_s,
    )
    calibration, calibration_provenance = _read_calibration(args.calibration)
    current_alignment = frame.attrs["clock_alignment"]
    calibration_offset = _clock_offset(calibration_provenance)
    current_offset = _clock_offset({"clock_alignment": current_alignment})
    if calibration_offset is None and not args.allow_unverified_calibration:
        raise RuntimeError(
            "calibration lacks timestamp-alignment provenance; rerun Stage 3 or pass --allow-unverified-calibration explicitly"
        )
    if calibration_offset is not None and current_offset is not None and abs(calibration_offset - current_offset) > 0.11:
        raise RuntimeError(
            "calibration clock offset does not match this replay; rerun Stage 3 with the same source and alignment settings"
        )
    calibration_end_s = _calibration_end_exclusive_s(calibration_provenance)
    if calibration_end_s is None and not args.allow_unverified_calibration:
        raise RuntimeError(
            "calibration lacks an exclusive time boundary; rerun Stage 3 with --calibration-end-seconds before evaluation"
        )
    if calibration_end_s is not None and calibration_end_s > args.start_seconds + 1e-9:
        raise RuntimeError(
            "calibration interval crosses the masked GNSS blackout; rerun Stage 3 with an earlier exclusive boundary"
        )
    history = select_blackout(
        frame, start_seconds=args.start_seconds - args.history_seconds, duration_seconds=args.history_seconds
    )
    blackout = select_blackout(frame, start_seconds=args.start_seconds, duration_seconds=args.duration_seconds)
    # The state at GNSS loss is an allowed aiding measurement. Pick the last
    # synchronized fix at that boundary, while keeping every later blackout
    # row out of the inference input.
    loss_timestamp_s = float(blackout.timestamp_s.iloc[0])
    loss_reference = frame.loc[frame.timestamp_s <= loss_timestamp_s + 1e-6].tail(1)
    initial_state = initial_state_from_preoutage_reference(loss_reference, calibration)
    # Ground truth stays in ``blackout`` for measurement/plotting only. The
    # integrators receive a structurally sensor-only view so a later label
    # cannot accidentally influence a denied-GNSS state estimate.
    blackout_inputs = blackout.drop(
        columns=[column for column in blackout.columns if column.startswith("gt_")]
    )
    model, normalization, portable_artifact = load_portable_onnx_velocity_artifact(
        args.model,
        args.normalization,
        args.model_manifest,
    )
    # This historical Driver B replay uses the older static-calibration
    # preprocessor. The current deployable artifact intentionally uses the
    # mobile-equivalent gravity + GNSS-kinematic yaw path and Driver B fails
    # that path's mount-quality gate. Refuse a silent feature-space mismatch
    # instead of publishing a plausible but invalid position plot.
    if "preprocessing_contract" in portable_artifact.training_provenance:
        raise RuntimeError(
            "Stage 12 Driver B replay is a legacy static-calibration benchmark and "
            "cannot evaluate the current runtime-equivalent kinematic model. "
            "Use the recording-disjoint Stage 5 report and a fixed-mount field drive "
            "for the current artifact."
        )
    residual = estimate_adaptive_residual(history, calibration, model, normalization)

    classical = classical_nhc_dead_reckoning(
        blackout_inputs, calibration, initial_state=initial_state
    )
    learned = learned_velocity_nhc_dead_reckoning(
        blackout_inputs, calibration, model, normalization, initial_state=initial_state
    )
    fused = adaptive_fused_replay(
        blackout_inputs, calibration, model, normalization, residual, initial_state=initial_state
    )
    measurements = {
        "classical_nhc": measure_drift(blackout, classical),
        "learned_speed_nhc": measure_drift(blackout, learned),
        "masked_gnss_adaptive_fusion": measure_drift(blackout, fused),
    }
    fusion_metrics = measurements["masked_gnss_adaptive_fusion"]

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
        "fusion_safeguard": {
            "mode": (
                "adaptive_residual"
                if residual.speed_adaptation_applied or residual.yaw_adaptation_applied
                else "learned_nhc_fallback_weak_preoutage_fit"
            ),
            "speed_adaptation_applied": residual.speed_adaptation_applied,
            "yaw_adaptation_applied": residual.yaw_adaptation_applied,
        },
        "provenance": {
            "portable_velocity_artifact": portable_artifact.as_dict(),
            "inference_backend": "onnxruntime CPUExecutionProvider",
            "calibration": file_provenance(args.calibration),
            "calibration_input_provenance": calibration_provenance or None,
            "calibration_alignment_verified": calibration_offset is not None,
            "calibration_temporal_boundary_verified": calibration_end_s is not None,
            "blackout_inference_input_excludes_ground_truth": True,
            "initial_state_source": "last_GNSS_fix_at_loss_boundary_vehicle_proxy",
            "initial_state_timestamp_s": loss_timestamp_s,
            "replay_clock_alignment": current_alignment,
            "smartphone_source": file_provenance(args.smartphone),
            "vehicle_source": file_provenance(args.vehicle),
            "smartphone_gnss_speed_unit": args.smartphone_gnss_speed_unit,
            "clock_offset_override_s": args.clock_offset_s,
            "max_rows": args.max_rows,
        },
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

    if not report["acceptance"]["passes"]:
        raise SystemExit(f"fusion drift target not met: {fusion_metrics.drift_percent:.2f}% >= 10%")

if __name__ == "__main__":
    main()
