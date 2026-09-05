"""Run Stage 7: map-match learned DR against an OSM road graph."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from idr_ml.calibration import CalibrationResult
from idr_ml.dead_reckoning import (
    DeadReckoningResult,
    initial_state_from_preoutage_reference,
    learned_velocity_nhc_dead_reckoning,
    measure_drift,
    select_blackout,
)
from idr_ml.iovnbd import load_synchronized_pair
from idr_ml.map_matching import RoadGraph, download_osm_roads, safe_hmm_map_match
from idr_ml.velocity_model import load_velocity_artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smartphone", type=Path, required=True)
    parser.add_argument("--vehicle", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, default=Path("ml/reports/stage3/calibration.json"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("ml/artifacts/velocity_cnn"))
    parser.add_argument("--osm", type=Path, default=Path("ml/data/osm/driver_b_heldout.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("ml/reports/stage7"))
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--start-seconds", type=float, default=430.0)
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    args = parser.parse_args()
    frame = load_synchronized_pair(args.smartphone, args.vehicle, nrows=args.max_rows)
    calibration_data = json.loads(args.calibration.read_text(encoding="utf-8"))
    calibration_data.pop("stage", None)
    calibration_data.pop("input_provenance", None)
    blackout = select_blackout(frame, start_seconds=args.start_seconds, duration_seconds=args.duration_seconds)
    if not args.osm.exists():
        margin = 0.003
        download_osm_roads(
            args.osm,
            south=float(blackout.gt_latitude_deg.min() - margin), west=float(blackout.gt_longitude_deg.min() - margin),
            north=float(blackout.gt_latitude_deg.max() + margin), east=float(blackout.gt_longitude_deg.max() + margin),
        )
    model, normalization = load_velocity_artifact(args.artifact_dir)
    calibration = CalibrationResult(**calibration_data)
    loss_reference = frame.loc[frame.timestamp_s <= float(blackout.timestamp_s.iloc[0]) + 1e-6].tail(1)
    blackout_inputs = blackout.drop(columns=[column for column in blackout if column.startswith("gt_")])
    learned = learned_velocity_nhc_dead_reckoning(
        blackout_inputs,
        calibration,
        model,
        normalization,
        initial_state=initial_state_from_preoutage_reference(loss_reference, calibration),
    )
    graph = RoadGraph.from_overpass(args.osm, origin_latitude=float(blackout.gt_latitude_deg.iloc[0]), origin_longitude=float(blackout.gt_longitude_deg.iloc[0]))
    match = safe_hmm_map_match(
        graph,
        np.asarray(learned.east_m),
        np.asarray(learned.north_m),
        heading_rad=np.asarray(learned.heading_rad),
        speed_mps=np.asarray(learned.speed_mps),
    )
    matched = DeadReckoningResult(
        timestamp_s=learned.timestamp_s, east_m=match.east_m.tolist(), north_m=match.north_m.tolist(),
        speed_mps=learned.speed_mps, heading_rad=learned.heading_rad, gyro_z_bias_rps=learned.gyro_z_bias_rps,
    )
    raw_metrics, matched_metrics = measure_drift(blackout, learned), measure_drift(blackout, matched)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    matched.as_frame().to_csv(args.output_dir / "trajectory_map_matched.csv", index=False)
    accepted_count = int(match.accepted.sum())
    rejection_counts = {
        reason: int(sum(item == reason for item in match.rejection_reasons))
        for reason in sorted({item for item in match.rejection_reasons if item is not None})
    }
    report = {
        "stage": 7,
        "road_segments": len(graph.segments),
        "raw": raw_metrics.as_dict(),
        "map_matched": matched_metrics.as_dict(),
        "map_safety": {
            "accepted_points": accepted_count,
            "total_points": len(match.accepted),
            "coverage_percent": 100.0 * accepted_count / len(match.accepted),
            "mean_confidence": float(match.confidence.mean()),
            "rejection_counts": rejection_counts,
            "endpoint_improved": matched_metrics.end_position_error_m < raw_metrics.end_position_error_m,
            "trajectory_rmse_improved": matched_metrics.trajectory_rmse_m < raw_metrics.trajectory_rmse_m,
        },
    }
    (args.output_dir / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    if accepted_count == 0:
        raise SystemExit("map matching rejected every point under the fail-closed safety rule")


if __name__ == "__main__":
    main()
