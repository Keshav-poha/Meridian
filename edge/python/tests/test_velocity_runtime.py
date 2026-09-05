from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from idr_edge import (
    EdgeRuntimeStatus,
    FixedWindowPreprocessor,
    OnnxVelocityRuntime,
    TelemetryFrame,
)


def _frame(timestamp_s: float, *, acceleration_x: float | None = None) -> TelemetryFrame:
    return TelemetryFrame(
        timestamp_s=timestamp_s,
        linear_acceleration_vehicle_mps2=(timestamp_s if acceleration_x is None else acceleration_x, 2.0, 3.0),
        gyroscope_vehicle_rps=(4.0, 5.0, 6.0),
        magnetic_direction_vehicle=(0.0, 0.0, 1.0),
    )


class _FakeSession:
    def __init__(self, normalized_speed: float | Exception) -> None:
        self.normalized_speed = normalized_speed
        self.call_count = 0

    def run(self, outputs: list[str], inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.call_count += 1
        assert outputs == ["forward_speed_normalized"]
        assert inputs["imu_window"].shape == (1, 9, 20)
        if isinstance(self.normalized_speed, Exception):
            raise self.normalized_speed
        return [np.asarray([[self.normalized_speed]], dtype=np.float32)]


class EdgeWindowingTest(unittest.TestCase):
    def _normalization_file(self, directory: str) -> Path:
        metadata = Path(directory) / "normalization.json"
        metadata.write_text(json.dumps({
            "feature_mean": [0.0] * 9,
            "feature_std": [1.0] * 9,
            "target_mean": 2.0,
            "target_std": 3.0,
            "sample_rate_hz": 10.0,
        }))
        return metadata

    def test_200hz_input_resamples_to_shared_10hz_onnx_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata = self._normalization_file(directory)
            preprocessor = FixedWindowPreprocessor(metadata)
            window = None
            for index in range(401):
                time_s = index / 200.0
                window = preprocessor.ingest(_frame(time_s))
            self.assertIsNotNone(window)
            assert window is not None
            self.assertEqual(window.shape, (1, 9, 20))
            self.assertTrue(np.allclose(window[0, 0], np.linspace(0.1, 2.0, 20)))
            self.assertEqual(preprocessor.denormalize_speed(1.5), 6.5)
            self.assertAlmostEqual(preprocessor.max_input_gap_s, 0.015)
            self.assertGreaterEqual(preprocessor.last_result.input_confidence, 0.99)

    def test_gap_resets_history_and_requires_new_contiguous_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preprocessor = FixedWindowPreprocessor(self._normalization_file(directory))
            for index in range(41):
                preprocessor.ingest_result(_frame(index / 200.0))

            reset = preprocessor.ingest_result(_frame(0.25))
            self.assertEqual(reset.status, EdgeRuntimeStatus.GAP_RESET)
            self.assertAlmostEqual(reset.gap_s or 0.0, 0.05)
            self.assertEqual(reset.input_frame_count, 1)
            self.assertIsNone(reset.values)

            result = reset
            for index in range(1, 401):
                result = preprocessor.ingest_result(_frame(0.25 + index / 200.0))
            self.assertEqual(result.status, EdgeRuntimeStatus.READY)
            self.assertIsNotNone(result.values)
            self.assertGreaterEqual(result.input_confidence, 0.99)

    def test_timestamp_replay_is_reported_without_corrupting_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preprocessor = FixedWindowPreprocessor(self._normalization_file(directory))
            preprocessor.ingest_result(_frame(0.0))
            preprocessor.ingest_result(_frame(0.005))
            rejected = preprocessor.ingest_result(_frame(0.005))
            self.assertEqual(rejected.status, EdgeRuntimeStatus.REJECTED_TIMESTAMP)
            self.assertEqual(rejected.input_frame_count, 2)
            accepted = preprocessor.ingest_result(_frame(0.010))
            self.assertEqual(accepted.status, EdgeRuntimeStatus.WARMING_UP)
            self.assertEqual(accepted.input_frame_count, 3)

            invalid = preprocessor.ingest_result("not-a-telemetry-frame")  # type: ignore[arg-type]
            self.assertEqual(invalid.status, EdgeRuntimeStatus.INVALID_INPUT)
            compatibility = FixedWindowPreprocessor(self._normalization_file(directory))
            compatibility.ingest(_frame(0.0))
            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                compatibility.ingest(_frame(0.0))

    def test_telemetry_frame_rejects_nonfinite_and_unnormalized_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "timestamp_s"):
            _frame(math.nan)
        with self.assertRaisesRegex(ValueError, "axes must all be finite"):
            TelemetryFrame(
                timestamp_s=0.0,
                linear_acceleration_vehicle_mps2=(math.inf, 0.0, 0.0),
                gyroscope_vehicle_rps=(0.0, 0.0, 0.0),
                magnetic_direction_vehicle=(0.0, 0.0, 1.0),
            )
        with self.assertRaisesRegex(ValueError, "normalized direction"):
            TelemetryFrame(
                timestamp_s=0.0,
                linear_acceleration_vehicle_mps2=(0.0, 0.0, 0.0),
                gyroscope_vehicle_rps=(0.0, 0.0, 0.0),
                magnetic_direction_vehicle=(7.0, 8.0, 9.0),
            )
        with self.assertRaisesRegex(ValueError, "supplied together"):
            TelemetryFrame(
                timestamp_s=0.0,
                linear_acceleration_vehicle_mps2=(0.0, 0.0, 0.0),
                gyroscope_vehicle_rps=(0.0, 0.0, 0.0),
                magnetic_direction_vehicle=(0.0, 0.0, 1.0),
                latitude_deg=28.0,
            )

    def test_frame_validation_freezes_mutable_sensor_axes(self) -> None:
        axes = [1.0, 2.0, 3.0]
        frame = TelemetryFrame(
            timestamp_s=0.0,
            linear_acceleration_vehicle_mps2=axes,
            gyroscope_vehicle_rps=(0.0, 0.0, 0.0),
            magnetic_direction_vehicle=(0.0, 0.0, 1.0),
        )
        axes[0] = 999.0
        self.assertEqual(frame.linear_acceleration_vehicle_mps2, (1.0, 2.0, 3.0))

    def test_structured_prediction_exposes_ready_clamped_and_rejected_states(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata = self._normalization_file(directory)
            runtime = OnnxVelocityRuntime(None, metadata, session=_FakeSession(1.5))
            prediction = runtime.last_prediction
            for index in range(401):
                prediction = runtime.ingest_prediction(_frame(index / 200.0))
            self.assertEqual(prediction.status, EdgeRuntimeStatus.READY)
            self.assertAlmostEqual(prediction.speed_mps or 0.0, 6.5)
            self.assertGreaterEqual(prediction.confidence, 0.99)
            self.assertEqual(runtime.last_prediction, prediction)

            clamped = OnnxVelocityRuntime(None, metadata, session=_FakeSession(-1.0))
            for index in range(401):
                prediction = clamped.ingest_prediction(_frame(index / 200.0))
            self.assertEqual(prediction.status, EdgeRuntimeStatus.OUTPUT_CLAMPED)
            self.assertEqual(prediction.speed_mps, 0.0)
            self.assertEqual(prediction.model_speed_mps, -1.0)
            self.assertEqual(prediction.confidence, 0.5)

            high_output = (100.0 - 2.0) / 3.0
            rejected = OnnxVelocityRuntime(None, metadata, session=_FakeSession(high_output))
            for index in range(401):
                prediction = rejected.ingest_prediction(_frame(index / 200.0))
            self.assertEqual(prediction.status, EdgeRuntimeStatus.REJECTED_MODEL_OUTPUT)
            self.assertIsNone(prediction.speed_mps)
            self.assertIsNone(rejected.ingest(_frame(2.005)))

            failing = OnnxVelocityRuntime(None, metadata, session=_FakeSession(RuntimeError("unavailable")))
            for index in range(401):
                prediction = failing.ingest_prediction(_frame(index / 200.0))
            self.assertEqual(prediction.status, EdgeRuntimeStatus.MODEL_ERROR)
            self.assertIsNone(prediction.speed_mps)
            self.assertEqual(prediction.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
