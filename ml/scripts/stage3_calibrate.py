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
    args = parser.parse_args()
    frame = load_synchronized_pair(args.smartphone, args.vehicle, nrows=args.max_rows)
    result = estimate_mount_calibration(frame)
    metrics = {"stage": 3, **result.as_dict()}
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
