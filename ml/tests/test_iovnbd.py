from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from idr_ml.iovnbd import build_fixed_windows, load_synchronized_pair
from idr_ml.plotting import write_stage2_sanity_svg


class IOVNBDPipelineTest(unittest.TestCase):
    def test_synchronized_pair_builds_windows_and_svg(self) -> None:
        count = 40
        sensor = pd.DataFrame(
            {
                "GPS latitude": [52.0 + i * 1e-6 for i in range(count)],
                "GPS longitude": [-1.5 + i * 1e-6 for i in range(count)],
                "GPS speed": [36.0] * count,
                "GPS accuracy": [3.0] * count,
                "Time since start": [i * 100 for i in range(count)],
                "Accelerometer X": [0.1] * count,
                "Accelerometer Y": [0.2] * count,
                "Accelerometer Z": [9.81] * count,
                "Gravity X": [0.0] * count,
                "Gravity Y": [0.0] * count,
                "Gravity Z": [9.81] * count,
                "Gyroscope (Yaw)": [0.01] * count,
                "Gyroscope (Pitch)": [0.02] * count,
                "Gyroscope (Roll)": [0.03] * count,
                "Magnetic field X": [1.0] * count,
                "Magnetic field Y": [2.0] * count,
                "Magnetic field Z": [3.0] * count,
                "Orientation (Yaw)": [20.0] * count,
                "Orientation (Pitch)": [0.0] * count,
                "Orientation (Roll)": [0.0] * count,
            }
        )
        vehicle = pd.DataFrame(
            {
                "Time since start of day": [50_000 + i * 0.1 for i in range(count)],
                "GPS Latitude": [52.0 + i * 1e-6 for i in range(count)],
                "GPS Longitude": [-1.5 + i * 1e-6 for i in range(count)],
                "GPS Velocity": [36.0] * count,
                "GPS Heading": [45.0] * count,
                "Yaw rate": [0.0] * count,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sensor_path, vehicle_path = root / "S.csv", root / "V.csv"
            sensor.to_csv(sensor_path, index=False)
            vehicle.to_csv(vehicle_path, index=False)
            synchronized = load_synchronized_pair(sensor_path, vehicle_path)
            windows = build_fixed_windows(synchronized, window_seconds=2.0, stride_seconds=0.2)
            output = write_stage2_sanity_svg(synchronized, root / "sanity.svg")
            self.assertTrue(output.exists())
        self.assertEqual(windows.features.shape[1:], (20, 9))
        self.assertEqual(windows.labels.shape[1], 4)


if __name__ == "__main__":
    unittest.main()
