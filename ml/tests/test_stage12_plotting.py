from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from idr_ml.plotting import write_drift_vs_distance_svg, write_speed_tracking_svg


class Stage12PlottingTest(unittest.TestCase):
    def test_speed_and_drift_plots_include_required_series_and_threshold(self) -> None:
        timestamp = np.asarray([100.0, 100.1, 100.2, 100.3])
        reference_speed = np.asarray([8.0, 9.0, 9.0, 7.0])
        estimated_speed = np.asarray([8.0, 8.8, 9.1, 7.2])
        distance = np.asarray([0.0, 0.8, 1.7, 2.4])
        error = np.asarray([0.0, 0.05, 0.12, 0.20])

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            speed_path = write_speed_tracking_svg(
                timestamp,
                reference_speed,
                estimated_speed,
                output_dir / "speed_tracking_plot.svg",
                title="Speed test",
                subtitle="Acceleration, cruise, braking",
            )
            drift_path = write_drift_vs_distance_svg(
                distance,
                error,
                output_dir / "drift_vs_distance_plot.svg",
                title="Drift test",
                subtitle="Benchmark ceiling",
            )

            speed_svg = speed_path.read_text(encoding="utf-8")
            drift_svg = drift_path.read_text(encoding="utf-8")

        self.assertIn("Ground-truth speed", speed_svg)
        self.assertIn("AI-assisted INS speed", speed_svg)
        self.assertIn("time (s)", speed_svg)
        self.assertIn("speed (m/s)", speed_svg)
        self.assertIn("Cumulative drift error", drift_svg)
        self.assertIn("10% benchmark ceiling", drift_svg)
        self.assertIn('stroke-dasharray="9 7"', drift_svg)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
