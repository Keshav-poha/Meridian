"""Run Stage 3: estimate the phone mounting orientation from IO-VNBD."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from idr_ml.calibration import estimate_mount_calibration
from idr_ml.iovnbd import load_synchronized_pair


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smartphone", type=Path, required=True)
    parser.add_argument("--vehicle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("ml/reports/stage3"))
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--smartphone-gnss-speed-unit", choices=("mps", "kmh"), default="mps")
    parser.add_argument("--clock-offset-s", type=float, default=None)
    parser.add_argument("--calibration-end-seconds", type=float, default=None)
    args = parser.parse_args()
    source_frame = load_synchronized_pair(
        args.smartphone,
        args.vehicle,
        nrows=args.max_rows,
        smartphone_gnss_speed_unit=args.smartphone_gnss_speed_unit,
        clock_offset_s=args.clock_offset_s,
    )
    if args.calibration_end_seconds is not None:
        if args.calibration_end_seconds <= float(source_frame.timestamp_s.min()):
            raise SystemExit("calibration end must be later than the first synchronized sample")
        frame = source_frame.loc[source_frame.timestamp_s < args.calibration_end_seconds].reset_index(drop=True)
        frame.attrs = source_frame.attrs.copy()
    else:
        frame = source_frame
    if len(frame) < 3:
        raise SystemExit("calibration interval has fewer than three synchronized samples")
    result = estimate_mount_calibration(frame)
    metrics = {
        "stage": 3,
        "input_provenance": {
            "clock_alignment": frame.attrs["clock_alignment"],
            "max_interpolation_gap_s": frame.attrs.get("max_interpolation_gap_s"),
            "smartphone_gnss_speed_unit": args.smartphone_gnss_speed_unit,
            "clock_offset_override_s": args.clock_offset_s,
            "max_rows": args.max_rows,
            "calibration_samples": len(frame),
            "calibration_time_start_s": float(frame.timestamp_s.min()),
            "calibration_time_end_exclusive_s": args.calibration_end_seconds,
        },
        **result.as_dict(),
    }
    if metrics["static_gravity_residual_deg"] > 3.0:
        raise SystemExit("static gravity alignment residual exceeds 3 degrees")
    if metrics["turning_samples"] < 20:
        raise SystemExit("insufficient turning samples for dynamic calibration")
    if metrics["dynamic_turn_correlation"] < 0.2:
        raise SystemExit("turning kinematics do not support a reliable yaw calibration")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "calibration.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
