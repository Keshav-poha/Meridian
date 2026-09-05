"""ONNX velocity inference with deterministic resampling for 100/200 Hz IMUs."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from typing import Any, Deque

import numpy as np

from .contracts import TelemetryFrame


class FixedWindowPreprocessor:
    """Resample calibrated IMU frames into the shared 2 s / 10 Hz contract."""

    def __init__(self, normalization_path: str | Path, *, window_seconds: float = 2.0, model_rate_hz: float = 10.0) -> None:
        self.window_seconds = window_seconds
        self.model_rate_hz = model_rate_hz
        self.window_samples = int(round(window_seconds * model_rate_hz))
        metadata = json.loads(Path(normalization_path).read_text(encoding="utf-8"))
        self.mean = np.asarray(metadata["feature_mean"], dtype=np.float32)
        self.std = np.maximum(np.asarray(metadata["feature_std"], dtype=np.float32), 1e-4)
        self.target_mean = float(metadata["target_mean"])
        self.target_std = float(metadata["target_std"])
        self._frames: Deque[TelemetryFrame] = deque()

    def ingest(self, frame: TelemetryFrame) -> np.ndarray | None:
        """Add one sample and return a normalized ONNX [1, 9, 20] input when ready."""
        if self._frames and frame.timestamp_s <= self._frames[-1].timestamp_s:
            raise ValueError("TelemetryFrame timestamps must be strictly increasing")
        self._frames.append(frame)
        cutoff = frame.timestamp_s - self.window_seconds - 0.1
        while self._frames and self._frames[0].timestamp_s < cutoff:
            self._frames.popleft()
        if not self._frames or self._frames[0].timestamp_s > frame.timestamp_s - self.window_seconds + 1e-6:
            return None
        timestamps = np.asarray([item.timestamp_s for item in self._frames], dtype=np.float64)
        raw = np.asarray([
            [
                *item.linear_acceleration_vehicle_mps2,
                *item.gyroscope_vehicle_rps,
                *item.magnetic_direction_vehicle,
            ]
            for item in self._frames
        ], dtype=np.float32)
        target = np.linspace(
            frame.timestamp_s - self.window_seconds + 1.0 / self.model_rate_hz,
            frame.timestamp_s, self.window_samples,
        )
        resampled = np.stack([np.interp(target, timestamps, raw[:, axis]) for axis in range(9)], axis=-1)
        normalized = (resampled.astype(np.float32) - self.mean) / self.std
        return normalized.T[np.newaxis, :, :]

    def denormalize_speed(self, value: float) -> float:
        return value * self.target_std + self.target_mean


class OnnxVelocityRuntime:
    """Minimal portable runtime; the caller owns calibration and navigation fusion."""

    def __init__(self, model_path: str | Path, normalizer_path: str | Path) -> None:
        try:
            import onnxruntime as ort
        except ImportError as error:  # pragma: no cover - environment dependent
            raise RuntimeError("Install onnxruntime to execute the edge ONNX graph") from error
        self._session: Any = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.preprocessor = FixedWindowPreprocessor(normalizer_path)

    def ingest(self, frame: TelemetryFrame) -> float | None:
        values = self.preprocessor.ingest(frame)
        if values is None:
            return None
        normalized_speed = float(self._session.run(["forward_speed_normalized"], {"imu_window": values})[0][0])
        return self.preprocessor.denormalize_speed(normalized_speed)
