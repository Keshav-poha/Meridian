"""Train MERIDIAN's bounded phone-IMU velocity model on audited recordings.

The required IO-VNBD vehicle telemetry provides the primary speed labels. One
quality-passed STRIDE smartphone session adds a separate phone, mount, road,
and vibration domain. Every recording is transformed with the same
gravity-plus-kinematic mount calibration implemented in the mobile app; bad
kinematic fits are recorded and excluded instead of silently poisoning labels.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from idr_ml.iovnbd import build_fixed_windows, load_synchronized_pair
from idr_ml.multidrive_velocity import GroupedVelocityDataset, train_multidrive_velocity_cnn
from idr_ml.preprocessing import VELOCITY_FEATURE_COLUMNS, runtime_equivalent_velocity_features
from idr_ml.stride import discover_stride_driving_sessions, load_stride_session
from idr_ml.velocity_model import file_provenance, save_velocity_artifact


def _windows_from_frame(
    name: str,
    frame: Any,
    *,
    minimum_turn_correlation: float,
) -> tuple[str, Any, dict[str, object]]:
    features, alignment = runtime_equivalent_velocity_features(
        frame,
        minimum_turn_correlation=minimum_turn_correlation,
    )
    windows = build_fixed_windows(features, feature_columns=VELOCITY_FEATURE_COLUMNS)
    return name, windows, {
        "name": name,
        "accepted": True,
        "windows": len(windows.features),
        "kinematic_mount_alignment": {
            "yaw_rad": alignment.yaw_rad,
            "turning_samples": alignment.turning_samples,
            "dynamic_turn_correlation": alignment.dynamic_turn_correlation,
            "heading_coverage_deg": alignment.heading_coverage_deg,
        },
    }


def _require_file(path: Path) -> Path:
    if not path.is_file():
        raise SystemExit(
            f"missing training input: {path}. Download the documented IO-VNBD/STRIDE source "
            "before running this reproducible training stage."
        )
    return path


def _append_iovnbd(
    *,
    name: str,
    phone: Path,
    vehicle: Path,
    groups: list[tuple[str, Any]],
    records: list[dict[str, object]],
    sources: list[dict[str, object]],
    minimum_turn_correlation: float,
    nrows: int | None = None,
) -> None:
    phone, vehicle = _require_file(phone), _require_file(vehicle)
    frame = load_synchronized_pair(phone, vehicle, nrows=nrows)
    try:
        group_name, windows, record = _windows_from_frame(
            name,
            frame,
            minimum_turn_correlation=minimum_turn_correlation,
        )
    except ValueError as error:
        records.append({"name": name, "accepted": False, "rejection_reason": str(error)})
    else:
        groups.append((group_name, windows))
        records.append(record)
    sources.append(
        {
            "dataset": "IO-VNBD",
            "group": name,
            "phone": file_provenance(phone),
            "vehicle": file_provenance(vehicle),
            "clock_alignment": frame.attrs.get("clock_alignment"),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iovnbd-driver-b-phone", type=Path, default=Path("ml/data/raw/iovnbd_m/S-M.csv"))
    parser.add_argument("--iovnbd-driver-b-vehicle", type=Path, default=Path("ml/data/raw/iovnbd_m/V-M.csv"))
    parser.add_argument("--iovnbd-driver-a-s1-phone", type=Path, default=Path("ml/data/raw/iovnbd_multidrive/driver_a_s1_phone.csv"))
    parser.add_argument("--iovnbd-driver-a-s1-vehicle", type=Path, default=Path("ml/data/raw/iovnbd_multidrive/driver_a_s1_vehicle.csv"))
    parser.add_argument(
        "--iovnbd-official-root",
        type=Path,
        default=Path(
            "ml/data/raw/iovnbd_official/Synchronised V abd S datasets/"
            "Categorised IOVNB Dataset/S (Driver A)"
        ),
    )
    parser.add_argument("--stride-root", type=Path, default=Path("ml/data/raw/stride/extracted/Road Data"))
    parser.add_argument("--driver-b-rows", type=int, default=5000)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--max-windows-per-group", type=int, default=6000)
    parser.add_argument("--minimum-turn-correlation", type=float, default=0.2)
    parser.add_argument("--orientation-jitter-degrees", type=float, default=12.0)
    parser.add_argument("--exclude-training-group", action="append", default=[])
    parser.add_argument(
        "--require-prior-baseline-win",
        action="store_true",
        help=(
            "Fail when the standalone IMU-only speed prior does not beat the "
            "mean-speed baseline. The deployed GNSS-anchored INS state does "
            "not use this prior as an absolute-speed reset."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("ml/reports/stage5_robust"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("ml/artifacts/velocity_cnn_robust"))
    args = parser.parse_args()
    if args.driver_b_rows < 1000 or args.max_windows_per_group < 100:
        raise SystemExit("driver-b-rows and max-windows-per-group are too small for robust training")
    if not 0.0 <= args.minimum_turn_correlation <= 1.0:
        raise SystemExit("minimum-turn-correlation must be in [0, 1]")

    sources: list[dict[str, object]] = []
    groups: list[tuple[str, Any]] = []
    records: list[dict[str, object]] = []
    _append_iovnbd(
        name="iovnbd_driver_b", phone=args.iovnbd_driver_b_phone,
        vehicle=args.iovnbd_driver_b_vehicle, nrows=args.driver_b_rows,
        groups=groups, records=records, sources=sources,
        minimum_turn_correlation=args.minimum_turn_correlation,
    )
    _append_iovnbd(
        name="iovnbd_driver_a_s1", phone=args.iovnbd_driver_a_s1_phone,
        vehicle=args.iovnbd_driver_a_s1_vehicle,
        groups=groups, records=records, sources=sources,
        minimum_turn_correlation=args.minimum_turn_correlation,
    )
    for session in ("S3a", "S3b", "S3c"):
        _append_iovnbd(
            name=f"iovnbd_driver_a_{session.lower()}",
            phone=args.iovnbd_official_root / session / f"S-{session}.csv",
            vehicle=args.iovnbd_official_root / session / f"V-{session}.csv",
            groups=groups, records=records, sources=sources,
            minimum_turn_correlation=args.minimum_turn_correlation,
        )

    selected_stride_sessions = {"1. Aggressive"}
    found_stride_sessions = {path.name: path for path in discover_stride_driving_sessions(args.stride_root)}
    missing_stride = selected_stride_sessions.difference(found_stride_sessions)
    if missing_stride:
        raise SystemExit(f"STRIDE corpus lacks required quality-audit sessions: {sorted(missing_stride)}")
    for session_name in sorted(selected_stride_sessions):
        session = found_stride_sessions[session_name]
        name = f"stride_{session_name.lower().replace(' ', '_').replace('.', '')}"
        frame = load_stride_session(session)
        try:
            group_name, windows, record = _windows_from_frame(
                name, frame, minimum_turn_correlation=args.minimum_turn_correlation
            )
        except ValueError as error:
            records.append({"name": name, "accepted": False, "rejection_reason": str(error)})
        else:
            groups.append((group_name, windows))
            records.append(record)
        sources.append(
            {
                "dataset": "STRIDE", "group": name, "session": str(session),
                "accelerometer": file_provenance(session / "Accelerometer.csv"),
                "gyroscope": file_provenance(session / "Gyroscope.csv"),
                "magnetometer": file_provenance(session / "Magnetometer.csv"),
                "location": file_provenance(session / "Location.csv"),
                "speed_label": "smartphone_gnss_speed_mps",
            }
        )

    validation_groups = ("iovnbd_driver_a_s3b",)
    test_groups = ("iovnbd_driver_a_s3c",)
    accepted_names = {name for name, _ in groups}
    if missing := set(validation_groups + test_groups).difference(accepted_names):
        raise SystemExit(f"a required held-out recording failed quality validation: {sorted(missing)}")
    excluded_training_groups = set(args.exclude_training_group)
    if invalid := excluded_training_groups.intersection(validation_groups + test_groups):
        raise SystemExit(f"cannot exclude a validation/test group from training corpus: {sorted(invalid)}")
    training_groups = [
        group for group in groups if group[0] not in excluded_training_groups
    ]
    if not training_groups:
        raise SystemExit("no training groups remain after exclusions")
    corpus = GroupedVelocityDataset.from_windows(training_groups, max_windows_per_group=args.max_windows_per_group)
    model, normalization, metrics = train_multidrive_velocity_cnn(
        corpus,
        validation_groups=validation_groups,
        test_groups=test_groups,
        epochs=args.epochs,
        maximum_orientation_jitter_deg=args.orientation_jitter_degrees,
    )
    if not metrics.output_bounds_verified:
        raise SystemExit("bounded model emitted an out-of-contract speed during held-out evaluation")
    prior_beats_baseline = (
        metrics.test_mae_mps < metrics.mean_speed_baseline_mae_mps
    )
    if args.require_prior_baseline_win and not prior_beats_baseline:
        raise SystemExit(
            "multi-session IMU-only speed prior did not beat the train-split mean-speed baseline"
        )
    save_velocity_artifact(model, normalization, args.artifact_dir)
    report = {
        "stage": 5,
        "input_provenance": {
            "preprocessing_contract": "runtime_equivalent_kinematic_vehicle_frame_v2",
            "training_corpus": {
                "sources": sources,
                "records": records,
                "validation_groups": list(validation_groups),
                "test_groups": list(test_groups),
                "max_windows_per_group": args.max_windows_per_group,
                "minimum_turn_correlation": args.minimum_turn_correlation,
                "excluded_training_groups": sorted(excluded_training_groups),
                "note": (
                    "IO-VNBD vehicle telemetry is the primary speed reference. "
                    "STRIDE smartphone GNSS speed is secondary domain-supervision only. "
                    "Splits are recording-disjoint; they do not claim a fleet-scale evaluation."
                ),
            },
            "output_bounds_mps": [0.0, 45.0],
        },
        "absolute_velocity_prior_beats_mean_baseline": prior_beats_baseline,
        "deployment_note": (
            "The app uses the CNN only as a bounded change-of-velocity residual "
            "inside a GNSS-anchored inertial state. A raw CNN speed is never "
            "allowed to overwrite the last measured GNSS speed during a blackout."
        ),
        **metrics.as_dict(),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report["trained_artifacts"] = {
        "model": file_provenance(args.artifact_dir / "velocity_cnn.pt"),
        "normalization": file_provenance(args.artifact_dir / "normalization.npz"),
    }
    (args.output_dir / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
