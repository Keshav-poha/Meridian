from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from idr_ml.dead_reckoning import (
    NavigationInitialState,
    runtime_gnss_anchored_dead_reckoning,
)
from idr_ml.preprocessing import VELOCITY_FEATURE_COLUMNS


class _ConstantPortableModel:
    def __init__(self, value: float) -> None:
        self.value = value

    def predict_normalized(self, channel_time: np.ndarray) -> np.ndarray:
        return np.full(len(channel_time), self.value, dtype=np.float32)


class RuntimeGnssAnchoredReplayTest(unittest.TestCase):
    def test_anchors_to_gnss_and_uses_only_bounded_model_deltas(self) -> None:
        count = 25
        frame = pd.DataFrame(
            {
                "timestamp_s": np.arange(count, dtype=float) / 10.0,
                "linear_accel_forward_mps2": np.ones(count),
                "gyro_down_rps": np.zeros(count),
                **{
                    channel: np.zeros(count, dtype=float)
                    for channel in VELOCITY_FEATURE_COLUMNS
                    if channel != "linear_accel_forward_mps2"
                },
            }
        )
        frame["linear_accel_forward_mps2"] = 1.0
        normalization = {
            "feature_mean": np.zeros(9, dtype=np.float32),
            "feature_std": np.ones(9, dtype=np.float32),
            "target_mean": np.asarray(18.0, dtype=np.float32),
            "target_std": np.asarray(1.0, dtype=np.float32),
            "sample_rate_hz": np.asarray(10.0, dtype=np.float32),
        }

        replay = runtime_gnss_anchored_dead_reckoning(
            frame,
            blackout_start_index=20,
            model=_ConstantPortableModel(0.0),
            normalization=normalization,
            initial_state=NavigationInitialState(
                speed_mps=4.0,
                heading_rad=0.0,
                gyro_z_bias_rps=0.0,
            ),
        )

        # A constant CNN output has no velocity derivative, so it cannot pull
        # the GNSS-anchored state toward the CNN's absolute 18 m/s prior.
        self.assertTrue(np.allclose(replay.model_speed_mps, 18.0))
        self.assertAlmostEqual(replay.dead_reckoning.speed_mps[0], 4.0)
        # The bounded CNN derivative may make a small acceleration-residual
        # correction, but cannot reset the state to its absolute prior.
        self.assertAlmostEqual(replay.dead_reckoning.speed_mps[-1], 4.364, places=6)
        self.assertTrue(
            np.all(np.asarray(replay.dead_reckoning.speed_mps) <= 45.0)
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
