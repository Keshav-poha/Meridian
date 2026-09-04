from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from idr_ml.calibration import CalibrationResult, estimate_mount_calibration
from idr_ml.dead_reckoning import classical_nhc_dead_reckoning, measure_drift
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

    def test_calibration_recovers_heading_offset(self) -> None:
        # A lateral vehicle acceleration of 2 m/s² at mount yaw +0.4 rad is
        # observed as R(-0.4)[0, 2] in the levelled phone frame.
        phone_x, phone_y = 2.0 * __import__("math").sin(0.4), 2.0 * __import__("math").cos(0.4)
        frame = pd.DataFrame(
            {
                "timestamp_s": [i * 0.1 for i in range(50)],
                "gravity_x_mps2": [0.0] * 50,
                "gravity_y_mps2": [0.0] * 50,
                "gravity_z_mps2": [9.81] * 50,
                "accel_x_mps2": [0.0] * 25 + [phone_x] * 25,
                "accel_y_mps2": [0.0] * 25 + [phone_y] * 25,
                "accel_z_mps2": [9.81] * 50,
                "gt_speed_mps": [10.0] * 50,
                "gt_heading_rad": [1.2] * 50,
                "phone_yaw_deg": [45.84] * 50,
                "gt_yaw_rate_rps": [0.0] * 25 + [0.2] * 25,
            }
        )
        result = estimate_mount_calibration(frame, min_idle_samples=20, min_turning_samples=20)
        self.assertLess(result.static_gravity_residual_deg, 1e-6)
        self.assertGreater(result.dynamic_turn_correlation, 0.99)
        self.assertAlmostEqual(result.yaw_rad, 0.4, places=2)

    def test_classical_nhc_replays_straight_constant_speed(self) -> None:
        count, speed, dt = 100, 5.0, 0.1
        frame = pd.DataFrame(
            {
                "timestamp_s": [i * dt for i in range(count)],
                "accel_x_mps2": [0.0] * count,
                "accel_y_mps2": [0.0] * count,
                "accel_z_mps2": [9.81] * count,
                "gravity_x_mps2": [0.0] * count,
                "gravity_y_mps2": [0.0] * count,
                "gravity_z_mps2": [9.81] * count,
                "gyro_x_rps": [0.0] * count,
                "gyro_y_rps": [0.0] * count,
                "gyro_z_rps": [0.0] * count,
                "gt_speed_mps": [speed] * count,
                "gt_heading_rad": [0.0] * count,
                "gt_latitude_deg": [52.0 + i * speed * dt / 111_320.0 for i in range(count)],
                "gt_longitude_deg": [-1.5] * count,
            }
        )
        calibration = CalibrationResult(
            body_to_vehicle=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            roll_rad=0.0, pitch_rad=0.0, yaw_rad=0.0, heading_sign=1,
            static_gravity_residual_deg=0.0, compass_heading_rmse_deg=0.0,
            dynamic_turn_correlation=1.0, dynamic_turn_rmse_mps2=0.0,
            idle_samples=0, turning_samples=0, static_source="test",
        )
        result = classical_nhc_dead_reckoning(frame, calibration)
        metrics = measure_drift(frame, result)
        self.assertLess(metrics.end_position_error_m, 0.01)
        self.assertAlmostEqual(metrics.update_rate_hz, 10.0, places=5)


if __name__ == "__main__":
    unittest.main()
