# MERIDIAN edge inference engine

The edge target starts as a Python reference runtime for deterministic replay and 200 Hz profiling. `cpp/` is the intentionally small CMake/ONNX Runtime integration point for a later hardware deployment; it consumes the same manifest and feature order as the mobile target.

The engine never imports training code. It accepts `TelemetryFrame` values whose raw feature order is defined by `shared/config/feature_spec.json`.
