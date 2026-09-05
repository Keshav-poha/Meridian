"""Train and evaluate a small 1-D CNN that estimates forward vehicle speed."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .calibration import CalibrationResult
from .dead_reckoning import classical_nhc_dead_reckoning
from .iovnbd import WindowedDataset


def file_sha256(path: str | Path) -> str:
    """Return the content hash used to bind an evaluation to an artifact."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_provenance(path: str | Path) -> dict[str, str | int]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"required artifact is missing: {source}")
    return {"path": str(source), "sha256": file_sha256(source), "bytes": source.stat().st_size}


@dataclass(frozen=True)
class PortableVelocityArtifact:
    """The verified, language-neutral artifact used by a replay."""

    model: dict[str, str | int]
    normalization: dict[str, str | int]
    manifest: dict[str, str | int]
    contract_version: str
    input_channels: tuple[str, ...]
    training_provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _torch() -> Any:
    try:
        import torch
    except ImportError as error:  # pragma: no cover - depends on local install
        raise RuntimeError(
            "PyTorch is required. Install it locally with: "
            "python -m pip install --target ml/.vendor torch --index-url https://download.pytorch.org/whl/cpu"
        ) from error
    return torch


def _model_class() -> Any:
    torch = _torch()

    class TinyVelocityCNN(torch.nn.Module):
        """~1.1k parameter causal-free CNN, suitable for on-device export."""

        def __init__(self, channels: int = 9) -> None:
            super().__init__()
            self.features = torch.nn.Sequential(
                torch.nn.Conv1d(channels, 16, kernel_size=5, padding=2),
                torch.nn.ReLU(),
                torch.nn.Conv1d(16, 16, kernel_size=3, padding=1, groups=4),
                torch.nn.ReLU(),
                torch.nn.AdaptiveAvgPool1d(1),
            )
            self.head = torch.nn.Linear(16, 1)

        def forward(self, values: Any) -> Any:
            return self.head(self.features(values).squeeze(-1)).squeeze(-1)

    return TinyVelocityCNN


@dataclass(frozen=True)
class VelocityTrainingResult:
    train_windows: int
    validation_windows: int
    test_windows: int
    epochs: int
    parameter_count: int
    validation_mae_mps: float
    test_mae_mps: float
    test_rmse_mps: float
    classical_speed_mae_mps: float
    split_guard_windows: int
    split_guard_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _split_indices(
    count: int,
    *,
    guard_windows: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Make temporal splits with a purge band around each split boundary.

    Adjacent sliding windows share almost all of their IMU samples. Omitting
    windows immediately following each boundary prevents validation or test
    inputs from inheriting raw samples from the preceding split.
    """
    if count < 100:
        raise ValueError("at least 100 windows are required for temporal train/validation/test splits")
    if guard_windows < 0:
        raise ValueError("guard_windows must be non-negative")
    train_end, validation_end = int(count * 0.70), int(count * 0.85)
    validation_start = train_end + guard_windows
    test_start = validation_end + guard_windows
    if validation_start >= validation_end or test_start >= count:
        raise ValueError("temporal split guard leaves no validation or test windows")
    return (
        np.arange(train_end),
        np.arange(validation_start, validation_end),
        np.arange(test_start, count),
    )


def _mae(prediction: np.ndarray, target: np.ndarray) -> float:
    return float(np.mean(np.abs(prediction - target)))


def train_velocity_cnn(
    windows: WindowedDataset,
    synchronized_frame: Any,
    calibration: CalibrationResult,
    *,
    epochs: int = 40,
    batch_size: int = 64,
    seed: int = 7,
) -> tuple[Any, dict[str, np.ndarray], VelocityTrainingResult]:
    """Train on earlier windows and report a strictly later temporal test split."""
    torch = _torch()
    torch.manual_seed(seed)
    torch.set_num_threads(min(4, torch.get_num_threads()))
    window_span_s = (windows.features.shape[1] - 1) / windows.sample_rate_hz
    timestamp_steps = np.diff(windows.timestamps_s)
    positive_steps = timestamp_steps[timestamp_steps > 0]
    if len(positive_steps) == 0:
        raise ValueError("window timestamps must be strictly increasing")
    # The minimum measured stride is conservative if rejected source gaps
    # have made some adjacent windows farther apart.
    window_stride_s = float(np.min(positive_steps))
    split_guard_windows = max(
        0,
        int(np.ceil(window_span_s / window_stride_s - 1e-9)) - 1,
    )
    train_idx, validation_idx, test_idx = _split_indices(
        len(windows.features), guard_windows=split_guard_windows
    )
    # Channel-wise statistics are fitted only on the temporal training block.
    train_features = windows.features[train_idx]
    mean = train_features.mean(axis=(0, 1), keepdims=True).astype(np.float32)
    std = train_features.std(axis=(0, 1), keepdims=True).astype(np.float32)
    std = np.maximum(std, 1e-4)
    x = ((windows.features - mean) / std).transpose(0, 2, 1).astype(np.float32)
    target = windows.labels[:, 0].astype(np.float32)
    target_mean, target_std = float(target[train_idx].mean()), float(target[train_idx].std())
    target_std = max(target_std, 1e-4)
    target_normalized = (target - target_mean) / target_std

    Model = _model_class()
    model = Model(channels=x.shape[1])
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    loss_fn = torch.nn.SmoothL1Loss()
    train_tensor = torch.from_numpy(x[train_idx])
    train_target = torch.from_numpy(target_normalized[train_idx])
    generator = torch.Generator().manual_seed(seed)
    for _ in range(epochs):
        for batch in torch.randperm(len(train_idx), generator=generator).split(batch_size):
            prediction = model(train_tensor[batch])
            loss = loss_fn(prediction, train_target[batch])
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
    model.eval()
    with torch.no_grad():
        normalized_prediction = model(torch.from_numpy(x)).cpu().numpy()
    prediction = normalized_prediction * target_std + target_mean

    # Classical comparison starts at the test period's last GNSS-aided state
    # and never uses later speed/heading/position inside its integration loop.
    test_start = float(windows.timestamps_s[test_idx[0]] - (windows.features.shape[1] - 1) / windows.sample_rate_hz)
    replay = synchronized_frame.loc[synchronized_frame.timestamp_s >= test_start].reset_index(drop=True)
    classical = classical_nhc_dead_reckoning(replay, calibration)
    classical_speed = np.interp(windows.timestamps_s[test_idx], replay.timestamp_s, classical.speed_mps)
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    result = VelocityTrainingResult(
        train_windows=len(train_idx), validation_windows=len(validation_idx), test_windows=len(test_idx),
        epochs=epochs, parameter_count=parameter_count,
        validation_mae_mps=_mae(prediction[validation_idx], target[validation_idx]),
        test_mae_mps=_mae(prediction[test_idx], target[test_idx]),
        test_rmse_mps=float(np.sqrt(np.mean(np.square(prediction[test_idx] - target[test_idx])))),
        classical_speed_mae_mps=_mae(classical_speed, target[test_idx]),
        split_guard_windows=split_guard_windows,
        split_guard_seconds=split_guard_windows * window_stride_s,
    )
    normalization = {
        "feature_mean": mean.reshape(-1), "feature_std": std.reshape(-1),
        "target_mean": np.asarray(target_mean, dtype=np.float32),
        "target_std": np.asarray(target_std, dtype=np.float32),
        "sample_rate_hz": np.asarray(windows.sample_rate_hz, dtype=np.float32),
    }
    return model, normalization, result


def save_velocity_artifact(model: Any, normalization: dict[str, np.ndarray], output_dir: str | Path) -> None:
    """Save weights and language-neutral normalization values for Stage 10."""
    torch = _torch()
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), destination / "velocity_cnn.pt")
    np.savez(destination / "normalization.npz", **normalization)


class PortableOnnxVelocityModel:
    """Small adapter that exposes committed ONNX output to replay code."""

    def __init__(self, session: Any, *, input_name: str, output_name: str) -> None:
        self._session = session
        self._input_name = input_name
        self._output_name = output_name

    def predict_normalized(self, channel_time: np.ndarray) -> np.ndarray:
        values = np.asarray(channel_time, dtype=np.float32)
        if values.ndim != 3 or values.shape[1:] != (9, 20):
            raise ValueError("portable ONNX velocity model requires [batch, 9, 20] input")
        # The exported graph has a fixed batch dimension of one. Calling it
        # per window keeps Stage 12 faithful to the deployed artifact rather
        # than silently substituting a PyTorch batch implementation.
        output: list[float] = []
        for window in values:
            value = self._session.run([self._output_name], {self._input_name: window[None, ...]})[0]
            output.append(float(np.asarray(value).reshape(-1)[0]))
        return np.asarray(output, dtype=np.float32)


def _portable_normalization(path: Path) -> dict[str, np.ndarray]:
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read portable velocity normalization {path}: {error}") from error
    required = {"feature_mean", "feature_std", "target_mean", "target_std", "sample_rate_hz"}
    if missing := required.difference(source):
        raise ValueError(f"portable velocity normalization missing: {sorted(missing)}")
    mean = np.asarray(source["feature_mean"], dtype=np.float32)
    std = np.asarray(source["feature_std"], dtype=np.float32)
    if mean.shape != (9,) or std.shape != (9,) or not np.isfinite(mean).all() or not np.isfinite(std).all():
        raise ValueError("portable velocity normalization must contain nine finite feature statistics")
    if np.any(std <= 0):
        raise ValueError("portable velocity normalization feature_std must be positive")
    target_mean = float(source["target_mean"])
    target_std = float(source["target_std"])
    sample_rate_hz = float(source["sample_rate_hz"])
    if not np.isfinite([target_mean, target_std, sample_rate_hz]).all() or target_std <= 0 or sample_rate_hz <= 0:
        raise ValueError("portable velocity normalization target/sample-rate values are invalid")
    return {
        "feature_mean": mean,
        "feature_std": std,
        "target_mean": np.asarray(target_mean, dtype=np.float32),
        "target_std": np.asarray(target_std, dtype=np.float32),
        "sample_rate_hz": np.asarray(sample_rate_hz, dtype=np.float32),
    }


def load_portable_onnx_velocity_artifact(
    model_path: str | Path,
    normalization_path: str | Path,
    manifest_path: str | Path,
) -> tuple[PortableOnnxVelocityModel, dict[str, np.ndarray], PortableVelocityArtifact]:
    """Load only a hash-bound portable ONNX model for reproducible replay.

    A local `ml/artifacts` checkpoint is deliberately not accepted here: it is
    ignored by Git and can describe an older preprocessing contract.
    """
    model_source, normalization_source, manifest_source = (
        Path(model_path),
        Path(normalization_path),
        Path(manifest_path),
    )
    for source in (model_source, normalization_source, manifest_source):
        if not source.is_file():
            raise FileNotFoundError(
                f"portable Stage 12 artifact missing: {source}. Run Stage 10 export and use shared/models artifacts."
            )
    try:
        manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read portable velocity manifest {manifest_source}: {error}") from error
    if manifest.get("format") != "onnx" or manifest.get("model_name") != "tiny_velocity_cnn":
        raise ValueError("portable Stage 12 artifact is not the expected tiny_velocity_cnn ONNX bundle")
    if Path(str(manifest.get("model_file", ""))).name != model_source.name:
        raise ValueError("portable manifest model_file does not match the selected ONNX model")
    normalization = manifest.get("normalization", {})
    if Path(str(normalization.get("file", ""))).name != normalization_source.name:
        raise ValueError("portable manifest normalization file does not match the selected normalizer")
    input_spec = manifest.get("input", {})
    expected_channels = (
        "linear_accel_forward_mps2", "linear_accel_right_mps2", "linear_accel_down_mps2",
        "gyro_forward_rps", "gyro_right_rps", "gyro_down_rps",
        "mag_forward_unit", "mag_right_unit", "mag_down_unit",
    )
    if tuple(input_spec.get("channels", [])) != expected_channels or input_spec.get("shape") != [1, 9, 20]:
        raise ValueError("portable ONNX manifest input contract is not the calibrated 9-channel 2-second model")
    hashes = manifest.get("artifact_sha256")
    if not isinstance(hashes, dict):
        raise ValueError("portable ONNX manifest lacks artifact_sha256; re-run Stage 10 export before evaluating")
    if hashes.get("model") != file_sha256(model_source) or hashes.get("normalization") != file_sha256(normalization_source):
        raise ValueError("portable ONNX model/normalizer hash does not match its manifest; refuse stale or mixed artifacts")
    training_provenance = manifest.get("training_provenance")
    calibration_provenance = (
        training_provenance.get("calibration") if isinstance(training_provenance, dict) else None
    )
    if (
        not isinstance(training_provenance, dict)
        or not isinstance(training_provenance.get("clock_alignment"), dict)
        or not isinstance(calibration_provenance, dict)
        or not isinstance(calibration_provenance.get("calibration_time_end_exclusive_s"), (int, float))
        or training_provenance.get("calibration_alignment_verified") is not True
        or not isinstance(training_provenance.get("stage5_metrics"), dict)
        or not isinstance(training_provenance.get("trained_artifacts"), dict)
    ):
        raise ValueError(
            "portable ONNX manifest lacks verified Stage 5 provenance; retrain Stage 5 and re-run Stage 10 export"
        )

    try:
        import onnxruntime as ort
    except ImportError as error:  # pragma: no cover - local runtime dependency
        raise RuntimeError("ONNX Runtime is required for Stage 12 portable evaluation; install onnxruntime in ml/.vendor") from error
    try:
        session = ort.InferenceSession(str(model_source), providers=["CPUExecutionProvider"])
    except Exception as error:  # pragma: no cover - runtime-specific diagnostics
        raise RuntimeError(f"cannot open portable ONNX model {model_source}: {error}") from error
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or len(outputs) != 1 or inputs[0].name != input_spec.get("name"):
        raise ValueError("portable ONNX graph does not match its manifest input/output schema")
    runtime_shape = tuple(inputs[0].shape)
    if runtime_shape != (1, 9, 20):
        raise ValueError(f"portable ONNX graph input shape {runtime_shape!r} is not (1, 9, 20)")
    return (
        PortableOnnxVelocityModel(session, input_name=inputs[0].name, output_name=outputs[0].name),
        _portable_normalization(normalization_source),
        PortableVelocityArtifact(
            model=file_provenance(model_source),
            normalization=file_provenance(normalization_source),
            manifest=file_provenance(manifest_source),
            contract_version=str(manifest.get("contract_version", "unknown")),
            input_channels=expected_channels,
            training_provenance=training_provenance,
        ),
    )


def load_velocity_artifact(artifact_dir: str | Path) -> tuple[Any, dict[str, np.ndarray]]:
    """Load the compact model and preprocessing captured at Stage 5."""
    torch = _torch()
    source = Path(artifact_dir)
    Model = _model_class()
    model = Model()
    model.load_state_dict(torch.load(source / "velocity_cnn.pt", map_location="cpu", weights_only=True))
    model.eval()
    with np.load(source / "normalization.npz") as data:
        normalization = {key: data[key] for key in data.files}
    return model, normalization


def predict_velocity_cnn(model: Any, features: np.ndarray, normalization: dict[str, np.ndarray]) -> np.ndarray:
    """Predict speed at each window endpoint from unnormalized IMU windows."""
    values = np.asarray(features, dtype=np.float32)
    if values.ndim != 3 or values.shape[2] != 9:
        raise ValueError("velocity prediction requires [batch, time, 9] unnormalized features")
    mean = np.asarray(normalization["feature_mean"], dtype=np.float32).reshape(1, 1, -1)
    std = np.asarray(normalization["feature_std"], dtype=np.float32).reshape(1, 1, -1)
    normalized = ((values - mean) / np.maximum(std, 1e-4)).transpose(0, 2, 1)
    if hasattr(model, "predict_normalized"):
        output = model.predict_normalized(normalized)
    else:
        torch = _torch()
        with torch.no_grad():
            output = model(torch.from_numpy(normalized)).cpu().numpy()
    return output * float(normalization["target_std"]) + float(normalization["target_mean"])
