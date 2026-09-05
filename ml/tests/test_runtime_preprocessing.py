from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from idr_ml.preprocessing import VELOCITY_FEATURE_COLUMNS, runtime_equivalent_velocity_features


class RuntimeEquivalentPreprocessingTest(unittest.TestCase):
    def test_matches_kinematic_mobile_mount_calibration_without_compass_yaw(self) -> None:
        speed_mps = 30.0
        heading_step_rad = math.radians(8.0)
        lateral_acceleration = speed_mps * heading_step_rad
        rows = []
        for index in range(25):
            rows.append(
                {
                    "timestamp_s": float(index),
                    # Seed gravity while still, then make phone +X vehicle-right.
                    "accel_x_mps2": 0.0 if index == 0 else lateral_acceleration,
                    "accel_y_mps2": 0.0,
                    "accel_z_mps2": 9.80665,
                    "gyro_x_rps": 0.0,
                    "gyro_y_rps": 0.0,
                    "gyro_z_rps": heading_step_rad,
                    # Deliberately unusable magnetic direction: yaw fitting
                    # must use kinematics, while the feature remains normalized.
                    "mag_x_ut": 500.0,
                    "mag_y_ut": -700.0,
                    "mag_z_ut": 300.0,
                    "gt_speed_mps": speed_mps,
                    "gt_heading_rad": index * heading_step_rad,
                }
            )
        frame = pd.DataFrame(rows)
        frame.attrs["sample_rate_hz"] = 1.0

        output, alignment = runtime_equivalent_velocity_features(frame)

        self.assertGreaterEqual(alignment.turning_samples, 20)
        self.assertGreaterEqual(alignment.dynamic_turn_correlation, 0.99)
        self.assertAlmostEqual(alignment.yaw_rad, math.pi / 2.0, places=3)
        values = output[VELOCITY_FEATURE_COLUMNS].to_numpy()
        self.assertTrue(np.allclose(values[:, 0], 0.0, atol=0.1))
        self.assertTrue(np.allclose(values[1:, 1], lateral_acceleration, atol=0.1))
        self.assertTrue(np.allclose(values[:, 2], 0.0, atol=0.05))

    def test_rejects_unproven_mount_yaw(self) -> None:
        count = 30
        frame = pd.DataFrame(
            {
                "timestamp_s": np.arange(count, dtype=float),
                "accel_x_mps2": np.zeros(count),
                "accel_y_mps2": np.zeros(count),
                "accel_z_mps2": np.full(count, 9.80665),
                "gyro_x_rps": np.zeros(count),
                "gyro_y_rps": np.zeros(count),
                "gyro_z_rps": np.zeros(count),
                "mag_x_ut": np.full(count, 30.0),
                "mag_y_ut": np.zeros(count),
                "mag_z_ut": np.zeros(count),
                "gt_speed_mps": np.full(count, 8.0),
                "gt_heading_rad": np.zeros(count),
            }
        )

        with self.assertRaisesRegex(ValueError, "kinematic turning samples"):
            runtime_equivalent_velocity_features(frame)


if __name__ == "__main__":
    unittest.main()
