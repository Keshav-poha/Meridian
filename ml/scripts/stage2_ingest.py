"""Run Stage 2: synchronize an IO-VNBD pair, build windows, and plot it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from idr_ml.iovnbd import build_fixed_windows, load_synchronized_pair
from idr_ml.plotting import write_stage2_sanity_svg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smartphone", type=Path, required=True)
    parser.add_argument("--vehicle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("ml/reports/stage2"))
    parser.add_argument("--max-rows", type=int, default=5000, help="small reproducible source subset")
    parser.add_argument("--sample-rate-hz", type=float, default=10.0)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--stride-seconds", type=float, default=0.2)
    args = parser.parse_args()
    frame = load_synchronized_pair(args.smartphone, args.vehicle, nrows=args.max_rows, target_rate_hz=args.sample_rate_hz)
    windows = build_fixed_windows(frame, window_seconds=args.window_seconds, stride_seconds=args.stride_seconds, sample_rate_hz=args.sample_rate_hz)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output_dir / "synchronized_subset.csv", index=False)
    write_stage2_sanity_svg(frame, args.output_dir / "sanity.svg")
    report = {
        "stage": 2,
        "source_rows_per_stream": args.max_rows,
        "synchronized_samples": int(len(frame)),
        "sample_rate_hz": args.sample_rate_hz,
        "window_shape": list(windows.features.shape),
        "label_shape": list(windows.labels.shape),
        "plot": "sanity.svg",
    }
    (args.output_dir / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
