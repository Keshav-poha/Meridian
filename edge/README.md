# MERIDIAN edge inference engine

The edge target is a Python ONNX Runtime reference engine for deterministic replay and 200 Hz profiling. `cpp/` remains the intentionally small CMake/ONNX Runtime integration point for a later hardware deployment; it consumes the same manifest and feature order as the mobile target.

The engine never imports training code. It accepts gravity-compensated,
body-to-vehicle-calibrated `TelemetryFrame` values whose feature order is
defined by `shared/config/feature_spec.json`. `FixedWindowPreprocessor`
retains the newest two seconds, resamples 100 Hz mobile or 200 Hz FOG-grade
input down to the model's 10 Hz window, and applies the shared normalizer.
`OnnxVelocityRuntime` returns a forward-speed estimate in m/s every time a
complete window is supplied.

## Input safety boundary

`TelemetryFrame` rejects a non-finite timestamp or axis, axes that are not
exactly three-dimensional, unpaired/out-of-range coordinates, and magnetic
vectors that do not look like the required normalized direction (norm 0.5–1.5).
The validated vectors are copied to immutable tuples, so a mutable caller
buffer cannot be changed after it enters an inference window.

The preprocessor additionally requires strictly increasing timestamps. Its
default is tuned for a 200 Hz FOG-grade stream: a gap larger than three nominal
samples (15 ms) discards the buffered window and produces no speed estimate.
That deliberately prevents interpolation across an unknown vehicle manoeuvre.
For a 100 Hz source, construct the runtime with
`expected_input_rate_hz=100`; the equivalent limit becomes 30 ms. A timestamp
replay can be reported without throwing through `ingest_result`; the original
`ingest` method continues to raise `ValueError` for backwards compatibility.

```python
prediction = runtime.ingest_prediction(frame)
if prediction.is_usable:
    fusion.accept_speed(prediction.speed_mps, prediction.confidence)
else:
    logger.warning("edge speed unavailable: %s", prediction.status.value)
```

`EdgePrediction` includes a status, reason, buffered-window coverage, input
frame count, optional detected gap, and a 0–1 **input-integrity confidence**.
That confidence is not an ML accuracy or navigation confidence: it only means
that the input window was continuous and the ONNX output was physically
bounded. Small negative forward-speed estimates are clamped to zero with a
degraded status; values below -3 m/s or above the model's 45 m/s deployment
ceiling are rejected rather than silently passed to fusion. The legacy
`OnnxVelocityRuntime.ingest(frame) -> float | None` API is preserved.

This remains a velocity-inference reference runtime, not a complete FOG-grade
IDR engine. The embedding edge process must still implement mount/attitude
calibration, motion and GNSS-quality gates, high-rate INS/UKF fusion, and
map-matching before it can claim the full 200 Hz navigation target.
