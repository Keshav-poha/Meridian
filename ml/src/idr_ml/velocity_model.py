"""Train and evaluate a small 1-D CNN that estimates forward vehicle speed."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .calibration import CalibrationResult
from .dead_reckoning import classical_nhc_dead_reckoning
from .iovnbd import WindowedDataset


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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _split_indices(count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if count < 100:
        raise ValueError("at least 100 windows are required for temporal train/validation/test splits")
    train_end, validation_end = int(count * 0.70), int(count * 0.85)
    return np.arange(train_end), np.arange(train_end, validation_end), np.arange(validation_end, count)


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
    train_idx, validation_idx, test_idx = _split_indices(len(windows.features))
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
