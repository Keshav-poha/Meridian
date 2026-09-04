"""Run Stage 10: export the learned velocity model for mobile and edge runtimes."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from time import perf_counter

import numpy as np

from idr_ml.model_export import (
    export_onnx, export_tflite, validate_onnx, validate_tflite, write_portable_metadata,
)
from idr_ml.velocity_model import load_velocity_artifact


def benchmark_edge_runtime(model_path: Path, normalization_path: Path) -> dict[str, float | int]:
    """Measure the Python ONNX reference on a 200 Hz FOG-grade input stream."""
    from idr_edge import OnnxVelocityRuntime, TelemetryFrame

    runtime = OnnxVelocityRuntime(model_path, normalization_path)
    output_count = 0
    start = perf_counter()
    # Ten seconds of synthetic, strictly ordered 200 Hz input exercises both
    # resampling and the actual ONNX Runtime invocation, not a no-op loop.
    for index in range(2_000):
        timestamp_s = index / 200.0
        speed = runtime.ingest(TelemetryFrame(
            timestamp_s=timestamp_s,
            acceleration_mps2=(0.15 * np.sin(timestamp_s), 0.0, 9.81),
            gyroscope_rps=(0.0, 0.0, 0.02),
            magnetometer_ut=(22.0, -4.0, 41.0),
        ))
        output_count += speed is not None
    elapsed_s = perf_counter() - start
    return {
        "input_rate_hz": 200.0,
        "inference_outputs": output_count,
        "reference_runtime_throughput_hz": output_count / elapsed_s,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=Path("ml/artifacts/velocity_cnn"))
    parser.add_argument("--feature-spec", type=Path, default=Path("shared/config/feature_spec.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("shared/models"))
    parser.add_argument("--mobile-assets", type=Path, default=Path("mobile/assets/models"))
    parser.add_argument("--report", type=Path, default=Path("ml/reports/stage10/metrics.json"))
    args = parser.parse_args()

    model, normalization = load_velocity_artifact(args.artifact_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_portable_metadata(normalization, args.feature_spec, args.output_dir)
    onnx_path = export_onnx(model, args.output_dir / "velocity_cnn.onnx")
    tflite_path = export_tflite(model, args.output_dir / "velocity_cnn.tflite")
    validations = [validate_onnx(model, onnx_path), validate_tflite(model, tflite_path)]

    args.mobile_assets.mkdir(parents=True, exist_ok=True)
    for filename in ("velocity_cnn.tflite", "velocity_cnn.normalization.json", "velocity_cnn.tflite.manifest.json"):
        shutil.copy2(args.output_dir / filename, args.mobile_assets / filename)
    edge_benchmark = benchmark_edge_runtime(onnx_path, args.output_dir / "velocity_cnn.normalization.json")
    if edge_benchmark["reference_runtime_throughput_hz"] < 200.0:
        raise SystemExit("edge ONNX reference runtime did not sustain the 200 Hz target")
    metrics = {
        "stage": 10,
        "model": "tiny_velocity_cnn",
        "onnx_bytes": onnx_path.stat().st_size,
        "tflite_bytes": tflite_path.stat().st_size,
        "exports": [validation.as_dict() for validation in validations],
        "edge_benchmark": edge_benchmark,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
