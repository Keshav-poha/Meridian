"""Benchmark seamless mode transitions under a replayed GNSS mask."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from idr_ml.mode_switch import SeamlessModeSwitcher


def main() -> None:
    output = Path("ml/reports/stage9")
    output.mkdir(parents=True, exist_ok=True)
    switcher = SeamlessModeSwitcher(reacquisition_seconds=0.5)
    samples = []
    for index in range(100):
        timestamp = index / 10.0
        dr = np.array([timestamp * 10.0, 0.0])
        gnss = np.array([timestamp * 10.0, 0.0]) if not 3.0 <= timestamp < 7.0 else None
        samples.append(switcher.update(timestamp, dr, gnss))
    latencies = [sample.transition_latency_ms for sample in samples]
    positions = np.array([[sample.east_m, sample.north_m] for sample in samples])
    jumps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    report = {
        "stage": 9,
        "max_transition_latency_ms": max(latencies),
        "mean_transition_latency_ms": float(np.mean(latencies)),
        "max_display_step_m": float(max(jumps)),
        "reacquisition_blend_seconds": 0.5,
    }
    if report["max_transition_latency_ms"] > 1.0:
        raise SystemExit("mode switch exceeded 1 ms decision budget")
    (output / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
