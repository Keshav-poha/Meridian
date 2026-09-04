# MERIDIAN edge inference engine

The edge target is a Python ONNX Runtime reference engine for deterministic replay and 200 Hz profiling. `cpp/` remains the intentionally small CMake/ONNX Runtime integration point for a later hardware deployment; it consumes the same manifest and feature order as the mobile target.

The engine never imports training code. It accepts `TelemetryFrame` values whose raw feature order is defined by `shared/config/feature_spec.json`. `FixedWindowPreprocessor` retains the newest two seconds, resamples 100 Hz mobile or 200 Hz FOG-grade input down to the model's 10 Hz window, and applies the shared normalizer. `OnnxVelocityRuntime` returns a forward-speed estimate in m/s every time a complete window is supplied.
