"""Run Stage 10: export the learned velocity model for mobile and edge runtimes."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from time import perf_counter

import numpy as np

from idr_ml.model_export import (
    bind_portable_manifest, export_onnx, export_tflite, validate_onnx, validate_tflite, write_portable_metadata,
)
from idr_ml.velocity_model import file_provenance, load_velocity_artifact


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
            linear_acceleration_vehicle_mps2=(0.15 * np.sin(timestamp_s), 0.0, 0.0),
            gyroscope_vehicle_rps=(0.0, 0.0, 0.02),
            magnetic_direction_vehicle=(0.45, -0.08, 0.89),
        ))
        output_count += speed is not None
    elapsed_s = perf_counter() - start
    return {
        "input_rate_hz": 200.0,
        "inference_outputs": output_count,
        "reference_runtime_throughput_hz": output_count / elapsed_s,
    }


def _load_training_provenance(metrics_path: Path, artifact_dir: Path) -> dict[str, object]:
    """Require Stage 10 to export the exact checkpoint documented by Stage 5."""
    if not metrics_path.is_file():
        raise FileNotFoundError(
            f"Stage 5 metrics are required for export: {metrics_path}. Run Stage 5 training first."
        )
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read Stage 5 metrics {metrics_path}: {error}") from error
    if not isinstance(metrics, dict) or metrics.get("stage") != 5:
        raise ValueError("training metrics must be a Stage 5 report")
    input_provenance = metrics.get("input_provenance")
    expected_artifacts = metrics.get("trained_artifacts")
    if not isinstance(input_provenance, dict) or not isinstance(expected_artifacts, dict):
        raise ValueError(
            "Stage 5 report lacks input or trained-artifact provenance; retrain before exporting"
        )
    verified_artifacts: dict[str, object] = {}
    for key, filename in (("model", "velocity_cnn.pt"), ("normalization", "normalization.npz")):
        expected = expected_artifacts.get(key)
        actual = file_provenance(artifact_dir / filename)
        if not isinstance(expected, dict) or expected.get("sha256") != actual["sha256"]:
            raise ValueError(
                f"Stage 5 {key} hash does not match {artifact_dir / filename}; refuse stale export"
            )
        verified_artifacts[key] = actual
    return {
        **input_provenance,
        "stage5_metrics": file_provenance(metrics_path),
        "trained_artifacts": verified_artifacts,
    }
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--artifact-dir", type=Path, default=Path("ml/artifacts/velocity_cnn"))
    parser.add_argument("--feature-spec", type=Path, default=Path("shared/config/feature_spec.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("shared/models"))
    parser.add_argument("--mobile-assets", type=Path, default=Path("mobile/assets/models"))
    parser.add_argument("--report", type=Path, default=Path("ml/reports/stage10/metrics.json"))
    parser.add_argument("--training-metrics", type=Path, default=Path("ml/reports/stage5/metrics.json"))
    args = parser.parse_args()

    training_provenance = _load_training_provenance(args.training_metrics, args.artifact_dir)
    model, normalization = load_velocity_artifact(args.artifact_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_portable_metadata(normalization, args.feature_spec, args.output_dir, training_provenance=training_provenance)
    onnx_path = export_onnx(model, args.output_dir / "velocity_cnn.onnx")
    tflite_path = export_tflite(model, args.output_dir / "velocity_cnn.tflite")
    normalization_path = args.output_dir / "velocity_cnn.normalization.json"
    bind_portable_manifest(
        args.output_dir / "velocity_cnn.onnx.manifest.json",
        model_path=onnx_path,
        normalization_path=normalization_path,
        feature_spec_path=args.feature_spec,
    )
    bind_portable_manifest(
        args.output_dir / "velocity_cnn.tflite.manifest.json",
        model_path=tflite_path,
        normalization_path=normalization_path,
        feature_spec_path=args.feature_spec,
    )
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
        "training_provenance": training_provenance,
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
