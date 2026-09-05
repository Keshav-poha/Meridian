from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd
import numpy as np

from idr_ml.calibration import CalibrationResult, estimate_mount_calibration
from idr_ml.dead_reckoning import (
    NavigationInitialState,
    classical_nhc_dead_reckoning,
    learned_velocity_nhc_dead_reckoning,
    measure_drift,
)
from idr_ml.fusion import estimate_adaptive_residual
from idr_ml.map_matching import RoadGraph, RoadSegment, hmm_map_match, safe_hmm_map_match
from idr_ml.iovnbd import (
    IOVNBDFormatError,
    build_fixed_windows,
    fit_phone_vehicle_clock_offset,
    load_synchronized_pair,
    synchronize_streams,
)
from idr_ml.plotting import write_stage2_sanity_svg, write_trajectory_svg
from idr_ml.preprocessing import VELOCITY_FEATURE_COLUMNS, calibrated_velocity_features
from idr_ml.velocity_model import _split_indices


class IOVNBDPipelineTest(unittest.TestCase):
    def test_temporal_split_purges_overlapping_sliding_windows(self) -> None:
        train, validation, test = _split_indices(100, guard_windows=9)

        self.assertEqual(train[-1], 69)
        self.assertEqual(validation[0], 79)
        self.assertEqual(validation[-1], 84)
        self.assertEqual(test[0], 94)
        # At 10 Hz, 20-sample windows with a two-sample stride share no raw
        # sample when their starts are at least ten window indices apart.
        self.assertGreaterEqual(validation[0] - train[-1], 10)
        self.assertGreaterEqual(test[0] - validation[-1], 10)

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
            synchronized = load_synchronized_pair(
                sensor_path,
                vehicle_path,
                clock_offset_s=0.0,
            )
            windows = build_fixed_windows(synchronized, window_seconds=2.0, stride_seconds=0.2)
            output = write_stage2_sanity_svg(synchronized, root / "sanity.svg")
            self.assertTrue(output.exists())
        self.assertEqual(windows.features.shape[1:], (20, 9))
        self.assertEqual(windows.labels.shape[1], 4)
        self.assertAlmostEqual(float(synchronized.gnss_speed_mps.iloc[0]), 36.0)
        self.assertEqual(synchronized.attrs["smartphone_gnss_speed_unit"], "mps")

    def test_timestamp_fitter_recovers_offset_without_row_alignment(self) -> None:
        time = np.arange(0.0, 60.0, 0.1)
        profile = 8.0 + 0.02 * time + 0.9 * np.sin(0.43 * time) + 0.35 * np.sin(1.07 * time)
        smartphone = pd.DataFrame({"elapsed_s": time, "gnss_speed_mps": 8.0 + 0.02 * (time - 2.0) + 0.9 * np.sin(0.43 * (time - 2.0)) + 0.35 * np.sin(1.07 * (time - 2.0))})
        vehicle = pd.DataFrame({"elapsed_s": time, "gt_speed_mps": profile, "gt_heading_deg": np.full(len(time), 45.0)})

        estimate = fit_phone_vehicle_clock_offset(smartphone, vehicle)
        self.assertAlmostEqual(estimate.offset_s, -2.0, places=1)
        self.assertGreater(float(estimate.correlation), 0.99)
        self.assertGreater(float(estimate.correlation_improvement), 0.015)
        synchronized = synchronize_streams(smartphone, vehicle)
        alignment = synchronized.attrs["clock_alignment"]
        self.assertAlmostEqual(alignment["vehicle_time_s_equals_phone_time_s_plus_offset_s"], -2.0, places=1)
        self.assertTrue(alignment["validation_passed"])

    def test_timestamp_fitter_rejects_flat_speed_trace(self) -> None:
        time = np.arange(0.0, 20.0, 0.1)
        smartphone = pd.DataFrame({"elapsed_s": time, "gnss_speed_mps": np.full(len(time), 4.0)})
        vehicle = pd.DataFrame({"elapsed_s": time, "gt_speed_mps": np.full(len(time), 4.0)})
        with self.assertRaisesRegex(IOVNBDFormatError, "clock alignment failed"):
            fit_phone_vehicle_clock_offset(smartphone, vehicle)

    def test_interpolation_marks_long_source_gap_missing(self) -> None:
        time = np.array([0.0, 0.1, 0.2, 1.0, 1.1])
        smartphone = pd.DataFrame({"elapsed_s": time, "gnss_speed_mps": np.arange(len(time)), "accel_x_mps2": time})
        vehicle = pd.DataFrame({"elapsed_s": time, "gt_speed_mps": np.arange(len(time)), "gt_heading_deg": np.zeros(len(time))})
        synchronized = synchronize_streams(
            smartphone, vehicle, clock_offset_s=0.0, max_interpolation_gap_s=0.25
        )

        self.assertTrue(np.isnan(synchronized.loc[np.isclose(synchronized.timestamp_s, 0.3), "accel_x_mps2"]).all())

    def test_fixed_windows_do_not_bridge_a_missing_sensor_sample(self) -> None:
        count = 60
        timestamp = np.arange(count, dtype=float) / 10.0
        frame = pd.DataFrame({
            "timestamp_s": timestamp,
            "accel_x_mps2": [np.nan if index == 25 else 0.0 for index in range(count)],
            "accel_y_mps2": np.zeros(count), "accel_z_mps2": np.full(count, 9.81),
            "gyro_x_rps": np.zeros(count), "gyro_y_rps": np.zeros(count), "gyro_z_rps": np.zeros(count),
            "mag_x_ut": np.ones(count), "mag_y_ut": np.ones(count), "mag_z_ut": np.ones(count),
            "gt_speed_mps": np.full(count, 5.0), "gt_heading_rad": np.zeros(count),
            "gt_latitude_deg": np.full(count, 52.0), "gt_longitude_deg": np.full(count, -1.5),
        })
        frame.attrs["sample_rate_hz"] = 10.0

        windows = build_fixed_windows(frame, window_seconds=2.0, stride_seconds=0.1)
        # A 2-second window ending from 2.5 through 4.4 seconds contains the
        # missing 2.5-second sample and must be excluded, not spliced together.
        self.assertTrue(np.all((windows.timestamps_s < 2.5) | (windows.timestamps_s > 4.4)))

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

    def test_weak_history_speed_fit_does_not_apply_a_fleet_scale(self) -> None:
        class ConstantModel:
            def predict_normalized(self, channel_time: np.ndarray) -> np.ndarray:
                return np.zeros(len(channel_time), dtype=np.float32)

        count = 30
        frame = pd.DataFrame({
            "timestamp_s": np.arange(count, dtype=float) / 10.0,
            "accel_x_mps2": np.zeros(count), "accel_y_mps2": np.zeros(count),
            "accel_z_mps2": np.full(count, 9.81),
            "gravity_x_mps2": np.zeros(count), "gravity_y_mps2": np.zeros(count),
            "gravity_z_mps2": np.full(count, 9.81),
            "gyro_x_rps": np.zeros(count), "gyro_y_rps": np.zeros(count),
            "gyro_z_rps": np.zeros(count),
            "mag_x_ut": np.ones(count), "mag_y_ut": np.zeros(count), "mag_z_ut": np.zeros(count),
            "gt_speed_mps": np.linspace(1.0, 4.0, count),
            "gt_heading_rad": np.zeros(count),
        })
        calibration = CalibrationResult(
            body_to_vehicle=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            roll_rad=0.0, pitch_rad=0.0, yaw_rad=0.0, heading_sign=1,
            static_gravity_residual_deg=0.0, compass_heading_rmse_deg=0.0,
            dynamic_turn_correlation=1.0, dynamic_turn_rmse_mps2=0.0,
            idle_samples=1, turning_samples=1, static_source="test",
        )
        normalization = {
            "feature_mean": np.zeros(9, dtype=np.float32),
            "feature_std": np.ones(9, dtype=np.float32),
            "target_mean": np.asarray(2.0, dtype=np.float32),
            "target_std": np.asarray(1.0, dtype=np.float32),
            "sample_rate_hz": np.asarray(10.0, dtype=np.float32),
        }

        residual = estimate_adaptive_residual(frame, calibration, ConstantModel(), normalization)

        self.assertFalse(residual.speed_adaptation_applied)
        self.assertEqual(residual.speed_scale, 1.0)
        self.assertIsNone(residual.speed_correlation)

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

    def test_learned_replay_accepts_sensor_only_blackout_input(self) -> None:
        class ConstantModel:
            def predict_normalized(self, channel_time: np.ndarray) -> np.ndarray:
                return np.zeros(len(channel_time), dtype=np.float32)

        count = 25
        frame = pd.DataFrame({
            "timestamp_s": np.arange(count, dtype=float) / 10.0,
            "accel_x_mps2": np.zeros(count), "accel_y_mps2": np.zeros(count),
            "accel_z_mps2": np.full(count, 9.81),
            "gravity_x_mps2": np.zeros(count), "gravity_y_mps2": np.zeros(count),
            "gravity_z_mps2": np.full(count, 9.81),
            "gyro_x_rps": np.zeros(count), "gyro_y_rps": np.zeros(count),
            "gyro_z_rps": np.zeros(count),
            "mag_x_ut": np.ones(count), "mag_y_ut": np.zeros(count), "mag_z_ut": np.zeros(count),
        })
        calibration = CalibrationResult(
            body_to_vehicle=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            roll_rad=0.0, pitch_rad=0.0, yaw_rad=0.0, heading_sign=1,
            static_gravity_residual_deg=0.0, compass_heading_rmse_deg=0.0,
            dynamic_turn_correlation=1.0, dynamic_turn_rmse_mps2=0.0,
            idle_samples=1, turning_samples=1, static_source="test",
        )
        normalization = {
            "feature_mean": np.zeros(9, dtype=np.float32),
            "feature_std": np.ones(9, dtype=np.float32),
            "target_mean": np.asarray(5.0, dtype=np.float32),
            "target_std": np.asarray(1.0, dtype=np.float32),
            "sample_rate_hz": np.asarray(10.0, dtype=np.float32),
        }
        state = NavigationInitialState(speed_mps=5.0, heading_rad=0.0, gyro_z_bias_rps=0.0)

        result = learned_velocity_nhc_dead_reckoning(
            frame, calibration, ConstantModel(), normalization, initial_state=state
        )

        self.assertEqual(len(result.speed_mps), count)
        self.assertTrue(np.allclose(result.speed_mps, 5.0))

    def test_velocity_features_remove_gravity_and_rotate_to_vehicle_frame(self) -> None:
        calibration = CalibrationResult(
            body_to_vehicle=[[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
            roll_rad=0.0, pitch_rad=0.0, yaw_rad=np.pi / 2, heading_sign=1,
            static_gravity_residual_deg=0.0, compass_heading_rmse_deg=0.0,
            dynamic_turn_correlation=1.0, dynamic_turn_rmse_mps2=0.0,
            idle_samples=1, turning_samples=1, static_source="test",
        )
        frame = pd.DataFrame({
            "accel_x_mps2": [3.0], "accel_y_mps2": [4.0], "accel_z_mps2": [12.0],
            "gravity_x_mps2": [0.0], "gravity_y_mps2": [0.0], "gravity_z_mps2": [10.0],
            "gyro_x_rps": [1.0], "gyro_y_rps": [2.0], "gyro_z_rps": [3.0],
            "mag_x_ut": [3.0], "mag_y_ut": [4.0], "mag_z_ut": [0.0],
        })
        features = calibrated_velocity_features(frame, calibration)
        self.assertTrue(np.allclose(
            features[VELOCITY_FEATURE_COLUMNS].to_numpy(),
            [[-4.0, 3.0, 2.0, -2.0, 1.0, 3.0, -0.8, 0.6, 0.0]],
        ))

    def test_hmm_map_matching_projects_to_continuous_road(self) -> None:
        graph = RoadGraph(
            [
                RoadSegment(np.array([0.0, 0.0]), np.array([100.0, 0.0]), 1),
                RoadSegment(np.array([0.0, 20.0]), np.array([100.0, 20.0]), 2),
            ]
        )
        east, north = hmm_map_match(graph, np.array([0.0, 20.0, 40.0]), np.array([2.0, 1.0, 3.0]))
        self.assertTrue(np.allclose(east, [0.0, 20.0, 40.0]))
        self.assertTrue(np.allclose(north, 0.0))

    def test_safe_map_matching_keeps_raw_point_when_far_from_any_road(self) -> None:
        graph = RoadGraph([RoadSegment(np.array([0.0, 0.0]), np.array([100.0, 0.0]), 1)])
        result = safe_hmm_map_match(graph, np.array([10.0, 20.0]), np.array([60.0, 60.0]))

        self.assertTrue(np.allclose(result.east_m, [10.0, 20.0]))
        self.assertTrue(np.allclose(result.north_m, [60.0, 60.0]))
        self.assertFalse(result.accepted.any())
        self.assertEqual(result.rejection_reasons, ("too_far_from_road", "too_far_from_road"))
        self.assertTrue(np.allclose(result.confidence, 0.0))

    def test_safe_map_matching_rejects_ambiguous_parallel_roads(self) -> None:
        graph = RoadGraph(
            [
                RoadSegment(np.array([0.0, 0.0]), np.array([100.0, 0.0]), 1),
                RoadSegment(np.array([0.0, 20.0]), np.array([100.0, 20.0]), 2),
            ]
        )
        result = safe_hmm_map_match(graph, np.array([10.0, 30.0]), np.array([10.0, 10.0]))

        self.assertTrue(np.allclose(result.east_m, [10.0, 30.0]))
        self.assertTrue(np.allclose(result.north_m, [10.0, 10.0]))
        self.assertFalse(result.accepted.any())
        self.assertEqual(result.rejection_reasons, ("ambiguous_nearby_roads", "ambiguous_nearby_roads"))

    def test_safe_map_matching_uses_heading_to_reject_cross_street(self) -> None:
        graph = RoadGraph([RoadSegment(np.array([0.0, 0.0]), np.array([100.0, 0.0]), 1)])
        result = safe_hmm_map_match(
            graph,
            np.array([10.0]),
            np.array([2.0]),
            heading_rad=np.array([np.pi / 2.0]),
            speed_mps=np.array([8.0]),
        )

        self.assertFalse(result.accepted[0])
        self.assertEqual(result.rejection_reasons[0], "heading_inconsistent")

    def test_trajectory_plot_is_written_with_multiple_traces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = write_trajectory_svg(
                [
                    ("ground truth", np.array([0.0, 3.0]), np.array([0.0, 4.0]), "#00ff00"),
                    ("prediction", np.array([0.0, 2.0]), np.array([0.0, 4.0]), "#0088ff"),
                ],
                Path(directory) / "trajectory.svg",
                title="test trajectory",
                subtitle="test data",
            )
            self.assertTrue(output.exists())
            content = output.read_text(encoding="utf-8")
        self.assertIn("ground truth", content)
        self.assertIn("prediction", content)


if __name__ == "__main__":
    unittest.main()
