"""Safety properties of the deployable velocity model."""

from __future__ import annotations

import unittest

import numpy as np

from idr_ml.velocity_model import _model_class, _torch


class BoundedVelocityModelTest(unittest.TestCase):
    def test_extreme_imu_windows_cannot_emit_speed_outside_contract(self) -> None:
        torch = _torch()
        model = _model_class()(
            target_mean=18.0,
            target_std=6.0,
            maximum_speed_mps=45.0,
        )
        with torch.no_grad():
            values = torch.from_numpy(
                np.asarray(
                    [
                        np.full((9, 20), -1e6, dtype=np.float32),
                        np.full((9, 20), 1e6, dtype=np.float32),
                    ]
                )
            )
            normalized = model(values).cpu().numpy()
        speed_mps = normalized * 6.0 + 18.0

        self.assertTrue(np.isfinite(speed_mps).all())
        self.assertGreaterEqual(float(speed_mps.min()), 0.0)
        self.assertLessEqual(float(speed_mps.max()), 45.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
