"""ONNX velocity inference with deterministic, guarded 100/200 Hz resampling."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Deque

import numpy as np

from .contracts import TelemetryFrame


class EdgeRuntimeStatus(str, Enum):
    """State of the most recent edge input or prediction attempt."""

    WARMING_UP = "warming_up"
    READY = "ready"
    GAP_RESET = "gap_reset"
    REJECTED_TIMESTAMP = "rejected_timestamp"
    INVALID_INPUT = "invalid_input"
    OUTPUT_CLAMPED = "output_clamped"
    REJECTED_MODEL_OUTPUT = "rejected_model_output"
    MODEL_ERROR = "model_error"


@dataclass(frozen=True)
class PreprocessResult:
    """A guarded windowing result exposed for diagnostics and scheduling."""

    values: np.ndarray | None
    status: EdgeRuntimeStatus
    timestamp_s: float | None
    window_coverage_s: float
    input_frame_count: int
    input_confidence: float
    reason: str
    gap_s: float | None = None

    @property
    def is_ready(self) -> bool:
        return self.status is EdgeRuntimeStatus.READY and self.values is not None


@dataclass(frozen=True)
class EdgePrediction:
    """Structured edge result; `confidence` measures input integrity, not ML accuracy.

    A full navigation stack must combine this with mount, motion, GNSS, and
    fusion confidence.  The value here only says whether a contiguous, valid
    IMU window was available and whether the model output was physically sane.
    """

    speed_mps: float | None
    status: EdgeRuntimeStatus
    confidence: float
    timestamp_s: float | None
    window_coverage_s: float
    input_frame_count: int
    reason: str
    model_speed_mps: float | None = None
    gap_s: float | None = None

    @property
    def is_usable(self) -> bool:
        return self.speed_mps is not None and self.confidence >= 0.5


class FixedWindowPreprocessor:
    """Resample contiguous calibrated IMU frames into the shared 2 s / 10 Hz contract.

    The default edge cadence is 200 Hz.  More than three missing nominal input
    samples (15 ms at 200 Hz) invalidates the history instead of allowing
    interpolation over an unknown manoeuvre.  A 100 Hz caller can opt in by
    setting ``expected_input_rate_hz=100``.
    """

    def __init__(
        self,
        normalization_path: str | Path,
        *,
        window_seconds: float = 2.0,
        model_rate_hz: float = 10.0,
        expected_input_rate_hz: float = 200.0,
        max_gap_samples: float = 3.0,
    ) -> None:
        if window_seconds <= 0.0 or model_rate_hz <= 0.0:
            raise ValueError("window_seconds and model_rate_hz must be positive")
        if expected_input_rate_hz <= 0.0 or max_gap_samples <= 0.0:
            raise ValueError("expected_input_rate_hz and max_gap_samples must be positive")

        self.window_seconds = float(window_seconds)
        self.model_rate_hz = float(model_rate_hz)
        self.window_samples = int(round(self.window_seconds * self.model_rate_hz))
        if self.window_samples < 2:
            raise ValueError("window must contain at least two model samples")
        self.expected_input_rate_hz = float(expected_input_rate_hz)
        self.max_gap_samples = float(max_gap_samples)
        self.max_input_gap_s = self.max_gap_samples / self.expected_input_rate_hz

        metadata = json.loads(Path(normalization_path).read_text(encoding="utf-8"))
        self.mean = np.asarray(metadata["feature_mean"], dtype=np.float32)
        self.std = np.maximum(np.asarray(metadata["feature_std"], dtype=np.float32), 1e-4)
        self.target_mean = float(metadata["target_mean"])
        self.target_std = float(metadata["target_std"])
        self.maximum_speed_mps = float(metadata.get("maximum_speed_mps", 45.0))
        if self.mean.shape != (9,) or self.std.shape != (9,):
            raise ValueError("normalization metadata must contain nine feature means and standard deviations")
        if not np.isfinite(self.mean).all() or not np.isfinite(self.std).all():
            raise ValueError("normalization feature statistics must be finite")
        if not math.isfinite(self.target_mean) or not math.isfinite(self.target_std) or self.target_std <= 0.0:
            raise ValueError("normalization target statistics must be finite and have positive standard deviation")
        if not math.isfinite(self.maximum_speed_mps) or self.maximum_speed_mps <= 0.0:
            raise ValueError("normalization maximum_speed_mps must be a finite positive value")

        self._frames: Deque[TelemetryFrame] = deque()
        self._last_result = PreprocessResult(
            values=None,
            status=EdgeRuntimeStatus.WARMING_UP,
            timestamp_s=None,
            window_coverage_s=0.0,
            input_frame_count=0,
            input_confidence=0.0,
            reason="Awaiting the first IMU frame",
        )

    @property
    def last_result(self) -> PreprocessResult:
        return self._last_result

    def reset(self, *, reason: str = "Manual reset") -> None:
        """Discard buffered history so a caller can explicitly restart warm-up."""

        self._frames.clear()
        self._last_result = PreprocessResult(
            values=None,
            status=EdgeRuntimeStatus.WARMING_UP,
            timestamp_s=None,
            window_coverage_s=0.0,
            input_frame_count=0,
            input_confidence=0.0,
            reason=reason,
        )

    def ingest_result(self, frame: TelemetryFrame) -> PreprocessResult:
        """Add a frame and return its status without raising on timestamp replay.

        ``TelemetryFrame`` validates finite axes at construction.  This method
        adds the stateful validation that only the preprocessor can know:
        strictly increasing timestamps and a continuous input stream.
        """

        if not isinstance(frame, TelemetryFrame):
            return self._store(PreprocessResult(
                values=None,
                status=EdgeRuntimeStatus.INVALID_INPUT,
                timestamp_s=None,
                window_coverage_s=self._coverage_s(),
                input_frame_count=len(self._frames),
                input_confidence=0.0,
                reason="Expected a validated TelemetryFrame",
            ))

        if self._frames:
            gap_s = frame.timestamp_s - self._frames[-1].timestamp_s
            if gap_s <= 0.0:
                return self._store(PreprocessResult(
                    values=None,
                    status=EdgeRuntimeStatus.REJECTED_TIMESTAMP,
                    timestamp_s=frame.timestamp_s,
                    window_coverage_s=self._coverage_s(),
                    input_frame_count=len(self._frames),
                    input_confidence=0.0,
                    reason="TelemetryFrame timestamps must be strictly increasing",
                    gap_s=gap_s,
                ))
            if gap_s > self.max_input_gap_s:
                self._frames.clear()
                self._frames.append(frame)
                return self._store(PreprocessResult(
                    values=None,
                    status=EdgeRuntimeStatus.GAP_RESET,
                    timestamp_s=frame.timestamp_s,
                    window_coverage_s=0.0,
                    input_frame_count=1,
                    input_confidence=0.0,
                    reason=(
                        f"Input gap of {gap_s:.6f}s exceeded the {self.max_input_gap_s:.6f}s limit; "
                        "window reset"
                    ),
                    gap_s=gap_s,
                ))

        self._frames.append(frame)
        cutoff = frame.timestamp_s - self.window_seconds - self.max_input_gap_s
        while self._frames and self._frames[0].timestamp_s < cutoff:
            self._frames.popleft()

        coverage_s = self._coverage_s()
        confidence = self._input_confidence(coverage_s)
        if coverage_s + 1e-9 < self.window_seconds:
            return self._store(PreprocessResult(
                values=None,
                status=EdgeRuntimeStatus.WARMING_UP,
                timestamp_s=frame.timestamp_s,
                window_coverage_s=coverage_s,
                input_frame_count=len(self._frames),
                input_confidence=confidence,
                reason="Awaiting a contiguous full model window",
            ))

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
            frame.timestamp_s,
            self.window_samples,
        )
        resampled = np.stack([np.interp(target, timestamps, raw[:, axis]) for axis in range(9)], axis=-1)
        normalized = (resampled.astype(np.float32) - self.mean) / self.std
        values = np.ascontiguousarray(normalized.T[np.newaxis, :, :], dtype=np.float32)
        return self._store(PreprocessResult(
            values=values,
            status=EdgeRuntimeStatus.READY,
            timestamp_s=frame.timestamp_s,
            window_coverage_s=coverage_s,
            input_frame_count=len(self._frames),
            input_confidence=confidence,
            reason="Contiguous normalized model window ready",
        ))

    def ingest(self, frame: TelemetryFrame) -> np.ndarray | None:
        """Compatibility API returning a normalized ONNX [1, 9, 20] input when ready.

        Existing callers still receive ``None`` while warming up or after a
        gap.  Timestamp replay remains a ``ValueError`` as it was before;
        callers that need a non-throwing status can use :meth:`ingest_result`.
        """

        result = self.ingest_result(frame)
        if result.status is EdgeRuntimeStatus.REJECTED_TIMESTAMP:
            raise ValueError(result.reason)
        if result.status is EdgeRuntimeStatus.INVALID_INPUT:
            raise ValueError(result.reason)
        return result.values

    def denormalize_speed(self, value: float) -> float:
        return value * self.target_std + self.target_mean

    def _coverage_s(self) -> float:
        if len(self._frames) < 2:
            return 0.0
        return max(0.0, self._frames[-1].timestamp_s - self._frames[0].timestamp_s)

    def _input_confidence(self, coverage_s: float) -> float:
        coverage_fraction = min(1.0, max(0.0, coverage_s / self.window_seconds))
        expected_frames = max(1.0, min(coverage_s, self.window_seconds) * self.expected_input_rate_hz + 1.0)
        density_fraction = min(1.0, len(self._frames) / expected_frames)
        return coverage_fraction * density_fraction

    def _store(self, result: PreprocessResult) -> PreprocessResult:
        self._last_result = result
        return result


class OnnxVelocityRuntime:
    """Portable forward-speed runtime with guarded input and output boundaries.

    It deliberately does not claim to be a full edge IDR/fusion system.  The
    caller still owns mount calibration, attitude estimation, GNSS quality, and
    the high-rate INS/fusion loop.
    """

    def __init__(
        self,
        model_path: str | Path | None,
        normalizer_path: str | Path,
        *,
        expected_input_rate_hz: float = 200.0,
        max_gap_samples: float = 3.0,
        max_forward_speed_mps: float | None = None,
        session: Any | None = None,
    ) -> None:
        if session is None:
            if model_path is None:
                raise ValueError("model_path is required when no ONNX session is supplied")
            try:
                import onnxruntime as ort
            except ImportError as error:  # pragma: no cover - environment dependent
                raise RuntimeError("Install onnxruntime to execute the edge ONNX graph") from error
            session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.preprocessor = FixedWindowPreprocessor(
            normalizer_path,
            expected_input_rate_hz=expected_input_rate_hz,
            max_gap_samples=max_gap_samples,
        )
        resolved_speed_ceiling = (
            self.preprocessor.maximum_speed_mps
            if max_forward_speed_mps is None
            else max_forward_speed_mps
        )
        if resolved_speed_ceiling <= 0.0 or not math.isfinite(resolved_speed_ceiling):
            raise ValueError("max_forward_speed_mps must be a finite positive value")
        self._session: Any = session
        self.max_forward_speed_mps = float(resolved_speed_ceiling)
        self._last_prediction = EdgePrediction(
            speed_mps=None,
            status=EdgeRuntimeStatus.WARMING_UP,
            confidence=0.0,
            timestamp_s=None,
            window_coverage_s=0.0,
            input_frame_count=0,
            reason="Awaiting the first IMU frame",
        )

    @property
    def last_prediction(self) -> EdgePrediction:
        return self._last_prediction

    def ingest_prediction(self, frame: TelemetryFrame) -> EdgePrediction:
        """Return a speed estimate or a concrete reason it is unsafe to use."""

        result = self.preprocessor.ingest_result(frame)
        if not result.is_ready:
            return self._store_prediction(EdgePrediction(
                speed_mps=None,
                status=result.status,
                confidence=result.input_confidence,
                timestamp_s=result.timestamp_s,
                window_coverage_s=result.window_coverage_s,
                input_frame_count=result.input_frame_count,
                reason=result.reason,
                gap_s=result.gap_s,
            ))

        assert result.values is not None
        try:
            output = self._session.run(["forward_speed_normalized"], {"imu_window": result.values})
            normalized_speed = float(np.asarray(output[0]).reshape(-1)[0])
            if not math.isfinite(normalized_speed):
                raise ValueError("model returned a non-finite normalized speed")
            model_speed_mps = self.preprocessor.denormalize_speed(normalized_speed)
            if not math.isfinite(model_speed_mps):
                raise ValueError("model returned a non-finite speed")
        except Exception as error:  # runtime/library errors are surfaced as status, not stale output
            return self._store_prediction(EdgePrediction(
                speed_mps=None,
                status=EdgeRuntimeStatus.MODEL_ERROR,
                confidence=0.0,
                timestamp_s=result.timestamp_s,
                window_coverage_s=result.window_coverage_s,
                input_frame_count=result.input_frame_count,
                reason=f"ONNX inference failed: {type(error).__name__}",
                gap_s=result.gap_s,
            ))

        if model_speed_mps > self.max_forward_speed_mps or model_speed_mps < -3.0:
            return self._store_prediction(EdgePrediction(
                speed_mps=None,
                status=EdgeRuntimeStatus.REJECTED_MODEL_OUTPUT,
                confidence=0.0,
                timestamp_s=result.timestamp_s,
                window_coverage_s=result.window_coverage_s,
                input_frame_count=result.input_frame_count,
                reason=(
                    f"Model speed {model_speed_mps:.3f} m/s is outside the allowed "
                    f"range [-3.0, {self.max_forward_speed_mps:.1f}]"
                ),
                model_speed_mps=model_speed_mps,
                gap_s=result.gap_s,
            ))

        if model_speed_mps < 0.0:
            return self._store_prediction(EdgePrediction(
                speed_mps=0.0,
                status=EdgeRuntimeStatus.OUTPUT_CLAMPED,
                confidence=min(result.input_confidence, 0.5),
                timestamp_s=result.timestamp_s,
                window_coverage_s=result.window_coverage_s,
                input_frame_count=result.input_frame_count,
                reason="Small negative forward-speed estimate clamped to stationary",
                model_speed_mps=model_speed_mps,
                gap_s=result.gap_s,
            ))

        return self._store_prediction(EdgePrediction(
            speed_mps=model_speed_mps,
            status=EdgeRuntimeStatus.READY,
            confidence=result.input_confidence,
            timestamp_s=result.timestamp_s,
            window_coverage_s=result.window_coverage_s,
            input_frame_count=result.input_frame_count,
            reason="Contiguous input and physically bounded model output",
            model_speed_mps=model_speed_mps,
            gap_s=result.gap_s,
        ))

    def ingest(self, frame: TelemetryFrame) -> float | None:
        """Compatibility API returning only usable forward speed in m/s."""

        return self.ingest_prediction(frame).speed_mps

    def _store_prediction(self, prediction: EdgePrediction) -> EdgePrediction:
        self._last_prediction = prediction
        return prediction
