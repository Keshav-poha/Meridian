"""Generate the reproducible, deployable-model IO-VNBD blackout benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from idr_ml.dead_reckoning import (
    NavigationInitialState,
    latlon_to_enu,
    measure_drift,
    runtime_gnss_anchored_dead_reckoning,
    select_blackout,
)
from idr_ml.iovnbd import load_synchronized_pair
from idr_ml.plotting import (
    write_drift_vs_distance_svg,
    write_speed_tracking_svg,
    write_trajectory_svg,
)
from idr_ml.preprocessing import VELOCITY_FEATURE_COLUMNS, runtime_equivalent_velocity_features
from idr_ml.velocity_model import file_provenance, load_portable_onnx_velocity_artifact


_DRIVER_A_ROOT = Path(
    "ml/data/raw/iovnbd_official/Synchronised V abd S datasets/"
    "Categorised IOVNB Dataset/S (Driver A)/S3c"
)


def _build_sensor_context(
    frame: pd.DataFrame,
    *,
    calibration_end_seconds: float,
    blackout_end_seconds: float,
) -> tuple[pd.DataFrame, object]:
    """Fit the fixed mount before loss, then expose sensor-only model input.

    The runtime-equivalent preprocessor needs GNSS speed/course solely to fit
    the fixed phone-to-vehicle transform. This function deliberately masks
    those references immediately after the chosen calibration boundary, well
    before the held-out GNSS blackout begins. Position labels are never passed
    into preprocessing or inference.
    """

    context = frame.loc[frame.timestamp_s <= blackout_end_seconds].copy()
    context = context.drop(
        columns=[
            column
            for column in ("gt_latitude_deg", "gt_longitude_deg", "gt_yaw_rate_rps")
            if column in context
        ]
    )
    calibration_mask = context.timestamp_s >= calibration_end_seconds
    context.loc[calibration_mask, ["gt_speed_mps", "gt_heading_rad"]] = np.nan
    features, alignment = runtime_equivalent_velocity_features(context)
    return features[["timestamp_s", *VELOCITY_FEATURE_COLUMNS]].copy(), alignment


def _cumulative_reference_distance(
    east_m: np.ndarray,
    north_m: np.ndarray,
) -> np.ndarray:
    increments = np.hypot(np.diff(east_m), np.diff(north_m))
    return np.concatenate([np.zeros(1, dtype=float), np.cumsum(increments)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smartphone", type=Path, default=_DRIVER_A_ROOT / "S-S3c.csv")
    parser.add_argument("--vehicle", type=Path, default=_DRIVER_A_ROOT / "V-S3c.csv")
    parser.add_argument("--model", type=Path, default=Path("shared/models/velocity_cnn.onnx"))
    parser.add_argument(
        "--normalization",
        type=Path,
        default=Path("shared/models/velocity_cnn.normalization.json"),
    )
    parser.add_argument(
        "--model-manifest",
        type=Path,
        default=Path("shared/models/velocity_cnn.onnx.manifest.json"),
    )
    parser.add_argument("--smartphone-gnss-speed-unit", choices=("mps", "kmh"), default="mps")
    parser.add_argument("--clock-offset-s", type=float, default=None)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--calibration-end-seconds", type=float, default=300.0)
    parser.add_argument("--start-seconds", type=float, default=1200.0)
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/benchmarks/iovnbd-driver-a-s3c-stage12"),
    )
    args = parser.parse_args()

    if args.duration_seconds <= 0:
        raise SystemExit("duration-seconds must be positive")
    if args.calibration_end_seconds <= 0 or args.calibration_end_seconds >= args.start_seconds:
        raise SystemExit("calibration-end-seconds must be positive and strictly before start-seconds")

    frame = load_synchronized_pair(
        args.smartphone,
        args.vehicle,
        nrows=args.max_rows,
        smartphone_gnss_speed_unit=args.smartphone_gnss_speed_unit,
        clock_offset_s=args.clock_offset_s,
    )
    blackout_reference = select_blackout(
        frame,
        start_seconds=args.start_seconds,
        duration_seconds=args.duration_seconds,
    )
    loss_timestamp_s = float(blackout_reference.timestamp_s.iloc[0])
    blackout_end_timestamp_s = float(blackout_reference.timestamp_s.iloc[-1])
    if args.calibration_end_seconds <= float(frame.timestamp_s.min()):
        raise SystemExit("calibration-end-seconds must follow the first synchronized sample")
    if args.calibration_end_seconds >= loss_timestamp_s:
        raise SystemExit("calibration boundary must be strictly before the first denied-GNSS sample")

    sensor_context, alignment = _build_sensor_context(
        frame,
        calibration_end_seconds=args.calibration_end_seconds,
        blackout_end_seconds=blackout_end_timestamp_s,
    )
    blackout_start_indices = np.flatnonzero(
        sensor_context.timestamp_s.to_numpy(dtype=float) >= loss_timestamp_s
    )
    if blackout_start_indices.size == 0:
        raise SystemExit("sensor context does not contain the requested blackout boundary")
    blackout_start_index = int(blackout_start_indices[0])
    if len(sensor_context) - blackout_start_index != len(blackout_reference):
        raise SystemExit("sensor context and reference blackout are not timestamp-aligned")

    # The final GNSS-aided speed/course is available at loss. It initializes
    # the state but is never included in the blackout feature context.
    loss_reference = frame.loc[frame.timestamp_s < loss_timestamp_s].tail(1)
    if loss_reference.empty:
        raise SystemExit("no GNSS-aided state is available before the blackout")
    initial_state = NavigationInitialState(
        speed_mps=float(loss_reference.gt_speed_mps.iloc[0]),
        heading_rad=float(loss_reference.gt_heading_rad.iloc[0]),
        # The mobile runtime integrates the calibrated vehicle-frame yaw rate
        # directly. It does not fit a blackout-specific gyro correction.
        gyro_z_bias_rps=0.0,
    )
    model, normalization, portable_artifact = load_portable_onnx_velocity_artifact(
        args.model,
        args.normalization,
        args.model_manifest,
    )
    ai_assisted = runtime_gnss_anchored_dead_reckoning(
        sensor_context,
        blackout_start_index=blackout_start_index,
        model=model,
        normalization=normalization,
        initial_state=initial_state,
    )
    inertial_only = runtime_gnss_anchored_dead_reckoning(
        sensor_context,
        blackout_start_index=blackout_start_index,
        model=model,
        normalization=normalization,
        initial_state=initial_state,
        use_model_residual=False,
    )
    ai_metrics = measure_drift(blackout_reference, ai_assisted.dead_reckoning)
    inertial_metrics = measure_drift(blackout_reference, inertial_only.dead_reckoning)

    gt_east, gt_north = latlon_to_enu(
        blackout_reference.gt_latitude_deg.to_numpy(dtype=float),
        blackout_reference.gt_longitude_deg.to_numpy(dtype=float),
    )
    ai_east = np.asarray(ai_assisted.dead_reckoning.east_m, dtype=float)
    ai_north = np.asarray(ai_assisted.dead_reckoning.north_m, dtype=float)
    cumulative_distance = _cumulative_reference_distance(gt_east, gt_north)
    cumulative_error = np.hypot(ai_east - gt_east, ai_north - gt_north)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    position_plot = write_trajectory_svg(
        [
            ("Ground truth", gt_east, gt_north, "#35d07f"),
            (
                "Forward-acceleration INS",
                np.asarray(inertial_only.dead_reckoning.east_m),
                np.asarray(inertial_only.dead_reckoning.north_m),
                "#f5b942",
            ),
            ("AI-assisted GNSS-anchored INS", ai_east, ai_north, "#2997ff"),
        ],
        args.output_dir / "position_plot.svg",
        title="MERIDIAN — IO-VNBD Driver A S3c masked-GNSS replay",
        subtitle=(
            f"{args.duration_seconds:.0f} s blackout at {ai_metrics.update_rate_hz:.1f} Hz; "
            f"endpoint drift {ai_metrics.end_position_error_m:.2f} m "
            f"({ai_metrics.drift_percent:.2f}%)"
        ),
    )
    speed_plot = write_speed_tracking_svg(
        blackout_reference.timestamp_s.to_numpy(dtype=float),
        blackout_reference.gt_speed_mps.to_numpy(dtype=float),
        np.asarray(ai_assisted.dead_reckoning.speed_mps, dtype=float),
        args.output_dir / "speed_tracking_plot.svg",
        title="MERIDIAN — speed tracking during GNSS blackout",
        subtitle="Ground-truth vehicle speed compared with the AI-assisted GNSS-anchored INS state",
    )
    drift_plot = write_drift_vs_distance_svg(
        cumulative_distance,
        cumulative_error,
        args.output_dir / "drift_vs_distance_plot.svg",
        title="MERIDIAN — cumulative drift against distance travelled",
        subtitle="Dashed line is the SIH 10% endpoint-drift benchmark ceiling",
    )

    trajectory = pd.DataFrame(
        {
            "timestamp_s": blackout_reference.timestamp_s,
            "ground_truth_east_m": gt_east,
            "ground_truth_north_m": gt_north,
            "forward_accel_ins_east_m": inertial_only.dead_reckoning.east_m,
            "forward_accel_ins_north_m": inertial_only.dead_reckoning.north_m,
            "ai_assisted_ins_east_m": ai_east,
            "ai_assisted_ins_north_m": ai_north,
            "ground_truth_speed_mps": blackout_reference.gt_speed_mps,
            "ai_assisted_ins_speed_mps": ai_assisted.dead_reckoning.speed_mps,
            "cnn_speed_prior_mps": ai_assisted.model_speed_mps,
            "cumulative_distance_m": cumulative_distance,
            "cumulative_drift_error_m": cumulative_error,
            "ten_percent_ceiling_m": cumulative_distance * 0.10,
        }
    )
    trajectory.to_csv(args.output_dir / "trajectory.csv", index=False, float_format="%.6f")
    metric_rows = [
        {"method": "forward_acceleration_ins", **inertial_metrics.as_dict()},
        {"method": "ai_assisted_gnss_anchored_ins", **ai_metrics.as_dict()},
    ]
    pd.DataFrame(metric_rows).to_csv(
        args.output_dir / "metrics.csv",
        index=False,
        float_format="%.6f",
    )
    report = {
        "stage": 12,
        "dataset": "IO-VNBD Driver A S3c",
        "split": "recording-disjoint held-out model-evaluation recording",
        "runtime_contract": "runtime_equivalent_kinematic_vehicle_frame_v2",
        "gnss_blackout": {
            "start_seconds": loss_timestamp_s,
            "duration_seconds": float(blackout_reference.timestamp_s.iloc[-1] - loss_timestamp_s),
        },
        "pre_outage_mount_calibration": {
            "end_exclusive_seconds": args.calibration_end_seconds,
            "turning_samples": alignment.turning_samples,
            "dynamic_turn_correlation": alignment.dynamic_turn_correlation,
            "heading_coverage_deg": alignment.heading_coverage_deg,
        },
        "provenance": {
            "portable_velocity_artifact": portable_artifact.as_dict(),
            "inference_backend": "onnxruntime CPUExecutionProvider",
            "blackout_model_input": ["timestamp_s", *VELOCITY_FEATURE_COLUMNS],
            "blackout_inference_input_excludes_ground_truth": True,
            "initial_state_source": "last_GNSS_aided_speed_and_course_before_loss",
            "initial_state_timestamp_s": float(loss_reference.timestamp_s.iloc[0]),
            "replay_clock_alignment": frame.attrs["clock_alignment"],
            "smartphone_source": file_provenance(args.smartphone),
            "vehicle_source": file_provenance(args.vehicle),
            "smartphone_gnss_speed_unit": args.smartphone_gnss_speed_unit,
            "clock_offset_override_s": args.clock_offset_s,
            "max_rows": args.max_rows,
        },
        "acceptance": {
            "max_drift_percent": 10.0,
            "observed_drift_percent": ai_metrics.drift_percent,
            "passes": ai_metrics.drift_percent < 10.0,
        },
        "metrics": {
            row["method"]: {key: value for key, value in row.items() if key != "method"}
            for row in metric_rows
        },
        "artifacts": {
            "position_plot": position_plot.name,
            "speed_tracking_plot": speed_plot.name,
            "drift_vs_distance_plot": drift_plot.name,
            "metrics_table": "metrics.csv",
            "trajectory": "trajectory.csv",
        },
    }
    (args.output_dir / "metrics.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "Stage 12 complete: "
        f"{report['dataset']} | "
        f"AI-assisted drift {ai_metrics.drift_percent:.2f}% | "
        f"{ai_metrics.update_rate_hz:.1f} Hz"
    )
    print(f"Artifacts: {args.output_dir}")

    if not report["acceptance"]["passes"]:
        raise SystemExit(
            f"AI-assisted GNSS-anchored INS drift target not met: "
            f"{ai_metrics.drift_percent:.2f}% >= 10%"
        )


if __name__ == "__main__":
    main()
