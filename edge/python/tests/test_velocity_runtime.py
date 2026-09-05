from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from idr_edge import FixedWindowPreprocessor, TelemetryFrame


class EdgeWindowingTest(unittest.TestCase):
    def test_200hz_input_resamples_to_shared_10hz_onnx_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "normalization.json"
            metadata.write_text(json.dumps({
                "feature_mean": [0.0] * 9, "feature_std": [1.0] * 9,
                "target_mean": 2.0, "target_std": 3.0, "sample_rate_hz": 10.0,
            }))
            preprocessor = FixedWindowPreprocessor(metadata)
            window = None
            for index in range(401):
                time_s = index / 200.0
                window = preprocessor.ingest(TelemetryFrame(
                    timestamp_s=time_s,
                    linear_acceleration_vehicle_mps2=(time_s, 2.0, 3.0),
                    gyroscope_vehicle_rps=(4.0, 5.0, 6.0),
                    magnetic_direction_vehicle=(7.0, 8.0, 9.0),
                ))
            self.assertIsNotNone(window)
            assert window is not None
            self.assertEqual(window.shape, (1, 9, 20))
            self.assertTrue(np.allclose(window[0, 0], np.linspace(0.1, 2.0, 20)))
            self.assertEqual(preprocessor.denormalize_speed(1.5), 6.5)


if __name__ == "__main__":
    unittest.main()
