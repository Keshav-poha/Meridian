"""Portable exports and parity checks for the trained velocity CNN.

The training model uses PyTorch grouped convolution. The ONNX export keeps
that graph. The equivalent TFLite graph expands it to a regular convolution
whose cross-group weights are fixed to zero, so the function is unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np

from .velocity_model import _torch, file_sha256


@dataclass(frozen=True)
class ExportValidation:
    """Numerical agreement of one exported graph with the training graph."""

    format: str
    max_absolute_error: float
    input_shape: list[int]
    output_shape: list[int]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tensorflow() -> Any:
    try:
        import tensorflow as tf
    except ImportError as error:  # pragma: no cover - depends on local install
        raise RuntimeError(
            "TensorFlow is required for the TFLite export. Install it locally with "
            "`py -3.13 -m pip install --target ml/.vendor tensorflow`.") from error
    return tf


def _portable_normalization(normalization: dict[str, np.ndarray]) -> dict[str, Any]:
    """Make the NPZ values consumable from Dart, C++, and Python."""
    portable = {
        "feature_mean": np.asarray(normalization["feature_mean"], dtype=np.float32).tolist(),
        "feature_std": np.asarray(normalization["feature_std"], dtype=np.float32).tolist(),
        "target_mean": float(np.asarray(normalization["target_mean"])),
        "target_std": float(np.asarray(normalization["target_std"])),
        "sample_rate_hz": float(np.asarray(normalization["sample_rate_hz"])),
    }
    if "maximum_speed_mps" in normalization:
        portable["maximum_speed_mps"] = float(np.asarray(normalization["maximum_speed_mps"]))
    return portable


def write_portable_metadata(
    normalization: dict[str, np.ndarray],
    feature_spec_path: str | Path,
    output_dir: str | Path,
    *,
    training_provenance: dict[str, Any],
) -> Path:
    """Write one language-neutral normalizer and manifests beside model files."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    calibration = training_provenance.get("calibration")
    legacy_provenance = (
        isinstance(training_provenance.get("clock_alignment"), dict)
        and isinstance(calibration, dict)
        and training_provenance.get("calibration_alignment_verified") is True
        and isinstance(calibration.get("calibration_time_end_exclusive_s"), (int, float))
    )
    corpus_provenance = (
        isinstance(training_provenance.get("training_corpus"), dict)
        and isinstance(training_provenance.get("preprocessing_contract"), str)
    )
    if not legacy_provenance and not corpus_provenance:
        raise ValueError(
            "training provenance must include either verified single-drive calibration "
            "or a documented multi-drive training corpus"
        )
    try:
        json.dumps(training_provenance)
    except (TypeError, ValueError) as error:
        raise ValueError("training provenance must be JSON serializable") from error
    spec = json.loads(Path(feature_spec_path).read_text(encoding="utf-8"))
    portable = _portable_normalization(normalization)
    (destination / "velocity_cnn.normalization.json").write_text(
        json.dumps(portable, indent=2) + "\n", encoding="utf-8")
    channels = spec["input_channels"]
    common = {
        "contract_version": spec["contract_version"],
        "model_name": "tiny_velocity_cnn",
        "model_file": None,
        "output": {
            "name": "forward_speed_normalized",
            "unit": "z_score", "de_normalized_unit": "mps",
            "bounds_mps": [0.0, float(portable.get("maximum_speed_mps", 45.0))],
        },
        "normalization": {
            "file": "velocity_cnn.normalization.json",
            "method": spec["normalization"]["method"],
        },
        "training_parameter_count": 961,
        "training_provenance": training_provenance,
    }
    manifests = {
        "onnx": {
            **common,
            "format": "onnx", "model_file": "velocity_cnn.onnx",
            "input": {"name": "imu_window", "shape": [1, len(channels), 20], "channels": channels,
                      "layout": "batch_channel_time"},
        },
        "tflite": {
            **common,
            "format": "tflite", "model_file": "velocity_cnn.tflite",
            "input": {"name": "imu_window", "shape": [1, 20, len(channels)], "channels": channels,
                      "layout": "batch_time_channel"},
            "implementation_note": (
                "The grouped training convolution is zero-expanded to a standard "
                "convolution for TFLite compatibility; the function is unchanged."
            ),
        },
    }
    for format_name, manifest in manifests.items():
        (destination / f"velocity_cnn.{format_name}.manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return destination / "velocity_cnn.normalization.json"


def bind_portable_manifest(
    manifest_path: str | Path,
    *,
    model_path: str | Path,
    normalization_path: str | Path,
    feature_spec_path: str | Path,
) -> Path:
    """Bind a portable manifest to exact model/preprocessing file contents."""
    destination = Path(manifest_path)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["artifact_sha256"] = {
        "model": file_sha256(model_path),
        "normalization": file_sha256(normalization_path),
        "feature_spec": file_sha256(feature_spec_path),
    }
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination


def export_onnx(model: Any, output_path: str | Path) -> Path:
    """Export the original PyTorch model to a fixed-size ONNX graph."""
    torch = _torch()
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    example = torch.zeros((1, 9, 20), dtype=torch.float32)
    torch.onnx.export(
        model, example, output, input_names=["imu_window"],
        output_names=["forward_speed_normalized"], opset_version=17, dynamo=False,
    )
    return output


def _build_tflite_equivalent(model: Any) -> Any:
    """Build a Keras graph with the exact trained PyTorch weights."""
    tf = _tensorflow()
    state = {name: value.detach().cpu().numpy() for name, value in model.state_dict().items()}
    input_values = tf.keras.Input(shape=(20, 9), dtype=tf.float32, name="imu_window")
    first = tf.keras.layers.Conv1D(16, 5, padding="same", activation="relu", name="conv_1")(input_values)
    second = tf.keras.layers.Conv1D(16, 3, padding="same", activation="relu", name="conv_2")(first)
    pooled = tf.keras.layers.GlobalAveragePooling1D(name="temporal_average")(second)
    logits = tf.keras.layers.Dense(1, name="speed_logits")(pooled)
    # Keep the deployment graph mathematically identical to TinyVelocityCNN:
    # the model emits a normalized value, but its de-normalized speed can never
    # leave the physical 0–45 m/s contract.
    output = tf.keras.layers.Lambda(
        lambda values: model.normalized_speed_min
        + (model.normalized_speed_max - model.normalized_speed_min)
        * tf.math.sigmoid(values),
        name="forward_speed_normalized",
    )(logits)
    keras_model = tf.keras.Model(input_values, output, name="tiny_velocity_cnn")
    first_kernel = np.transpose(state["features.0.weight"], (2, 1, 0))
    second_kernel = np.zeros((3, 16, 16), dtype=np.float32)
    grouped_weight = state["features.2.weight"]
    for group in range(4):
        start = group * 4
        second_kernel[:, start:start + 4, start:start + 4] = np.transpose(
            grouped_weight[start:start + 4], (2, 1, 0))
    keras_model.get_layer("conv_1").set_weights([first_kernel, state["features.0.bias"]])
    keras_model.get_layer("conv_2").set_weights([second_kernel, state["features.2.bias"]])
    keras_model.get_layer("speed_logits").set_weights([
        state["head.weight"].T, state["head.bias"],
    ])
    return keras_model


def export_tflite(model: Any, output_path: str | Path) -> Path:
    """Export a float32, device-compatible TFLite model."""
    tf = _tensorflow()
    converter = tf.lite.TFLiteConverter.from_keras_model(_build_tflite_equivalent(model))
    converter.optimizations = []  # Keep exact float32 parity with the checkpoint.
    encoded = converter.convert()
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    return output


def _reference_prediction(model: Any, channel_time_input: np.ndarray) -> np.ndarray:
    torch = _torch()
    with torch.no_grad():
        return model(torch.from_numpy(channel_time_input.astype(np.float32))).cpu().numpy()


def validate_onnx(model: Any, model_path: str | Path) -> ExportValidation:
    """Check schema and compute ONNX-vs-PyTorch numerical agreement."""
    import onnx
    from onnx.reference import ReferenceEvaluator

    rng = np.random.default_rng(17)
    values = rng.normal(size=(1, 9, 20)).astype(np.float32)
    expected = _reference_prediction(model, values)
    onnx_model = onnx.load(str(model_path))
    onnx.checker.check_model(onnx_model)
    actual = ReferenceEvaluator(onnx_model).run(None, {"imu_window": values})[0]
    return ExportValidation(
        format="onnx", max_absolute_error=float(np.max(np.abs(expected - actual))),
        input_shape=list(values.shape), output_shape=list(np.asarray(actual).shape),
    )


def validate_tflite(model: Any, model_path: str | Path) -> ExportValidation:
    """Run TFLite with the mobile layout and compare it to PyTorch."""
    tf = _tensorflow()
    rng = np.random.default_rng(17)
    channel_time = rng.normal(size=(1, 9, 20)).astype(np.float32)
    expected = _reference_prediction(model, channel_time).reshape(1, 1)
    interpreter = tf.lite.Interpreter(model_path=str(model_path))
    interpreter.allocate_tensors()
    input_detail = interpreter.get_input_details()[0]
    output_detail = interpreter.get_output_details()[0]
    interpreter.set_tensor(input_detail["index"], channel_time.transpose(0, 2, 1))
    interpreter.invoke()
    actual = interpreter.get_tensor(output_detail["index"])
    return ExportValidation(
        format="tflite", max_absolute_error=float(np.max(np.abs(expected - actual))),
        input_shape=[int(value) for value in input_detail["shape"]],
        output_shape=[int(value) for value in output_detail["shape"]],
    )
