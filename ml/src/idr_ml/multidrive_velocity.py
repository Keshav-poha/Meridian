"""Leakage-safe multi-drive training for the deployable velocity CNN."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np

from .iovnbd import WindowedDataset
from .velocity_model import _model_class, _torch


@dataclass(frozen=True)
class GroupedVelocityDataset:
    """Fixed IMU windows labelled with their independent recording identity."""

    features: np.ndarray
    speed_mps: np.ndarray
    groups: np.ndarray
    sample_rate_hz: float

    @classmethod
    def from_windows(
        cls,
        datasets: Iterable[tuple[str, WindowedDataset]],
        *,
        max_windows_per_group: int | None = 6000,
    ) -> "GroupedVelocityDataset":
        features: list[np.ndarray] = []
        speeds: list[np.ndarray] = []
        groups: list[np.ndarray] = []
        rate: float | None = None
        for name, windows in datasets:
            if not name:
                raise ValueError("every training dataset needs a non-empty group name")
            if windows.features.ndim != 3 or windows.features.shape[1:] != (20, 9):
                raise ValueError(f"{name} does not satisfy the 20x9 velocity-window contract")
            if rate is None:
                rate = windows.sample_rate_hz
            elif not np.isclose(rate, windows.sample_rate_hz):
                raise ValueError("all training drives must use the same model sample rate")
            select = np.arange(len(windows.features))
            if max_windows_per_group is not None and len(select) > max_windows_per_group:
                # Evenly spaced deterministic subsampling avoids a long drive
                # drowning out a distinct phone, mount, or road condition.
                select = np.linspace(0, len(select) - 1, max_windows_per_group).round().astype(int)
            if not len(select):
                continue
            features.append(windows.features[select].astype(np.float32, copy=False))
            speeds.append(windows.labels[select, 0].astype(np.float32, copy=False))
            groups.append(np.full(len(select), name, dtype=object))
        if not features or rate is None:
            raise ValueError("no valid windows were available for multi-drive training")
        return cls(
            features=np.concatenate(features, axis=0),
            speed_mps=np.concatenate(speeds, axis=0),
            groups=np.concatenate(groups, axis=0),
            sample_rate_hz=float(rate),
        )


@dataclass(frozen=True)
class MultiDriveVelocityTrainingResult:
    train_windows: int
    validation_windows: int
    test_windows: int
    train_groups: tuple[str, ...]
    validation_groups: tuple[str, ...]
    test_groups: tuple[str, ...]
    epochs: int
    selected_epoch: int
    parameter_count: int
    validation_mae_mps: float
    test_mae_mps: float
    test_rmse_mps: float
    mean_speed_baseline_mae_mps: float
    maximum_predicted_speed_mps: float
    output_bounds_verified: bool
    orientation_jitter_degrees: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mae(prediction: np.ndarray, target: np.ndarray) -> float:
    return float(np.mean(np.abs(prediction - target)))


def _rotation_matrices(
    torch: Any,
    count: int,
    *,
    maximum_yaw_rad: float,
    maximum_tilt_rad: float,
    device: Any,
) -> Any:
    """Small residual mount errors for calibrated-frame augmentation."""

    yaw = (torch.rand(count, device=device) * 2.0 - 1.0) * maximum_yaw_rad
    pitch = (torch.rand(count, device=device) * 2.0 - 1.0) * maximum_tilt_rad
    roll = (torch.rand(count, device=device) * 2.0 - 1.0) * maximum_tilt_rad
    cy, sy = torch.cos(yaw), torch.sin(yaw)
    cp, sp = torch.cos(pitch), torch.sin(pitch)
    cr, sr = torch.cos(roll), torch.sin(roll)
    rz = torch.stack(
        (cy, -sy, torch.zeros_like(cy), sy, cy, torch.zeros_like(cy), torch.zeros_like(cy), torch.zeros_like(cy), torch.ones_like(cy)),
        dim=1,
    ).reshape(-1, 3, 3)
    ry = torch.stack(
        (cp, torch.zeros_like(cp), sp, torch.zeros_like(cp), torch.ones_like(cp), torch.zeros_like(cp), -sp, torch.zeros_like(cp), cp),
        dim=1,
    ).reshape(-1, 3, 3)
    rx = torch.stack(
        (torch.ones_like(cr), torch.zeros_like(cr), torch.zeros_like(cr), torch.zeros_like(cr), cr, -sr, torch.zeros_like(cr), sr, cr),
        dim=1,
    ).reshape(-1, 3, 3)
    return torch.bmm(rz, torch.bmm(ry, rx))


def _augment_vehicle_frame(
    torch: Any,
    values: Any,
    *,
    feature_mean: Any,
    feature_std: Any,
    maximum_orientation_jitter_deg: float,
) -> Any:
    """Apply plausible residual mount/noise perturbations only while training."""

    # Rotate in SI units, then put the result back into the training z-score
    # space. Rotating normalized channels would mix unrelated scale factors and
    # create an unphysical augmentation.
    batch = values * feature_std + feature_mean
    radians = np.deg2rad(maximum_orientation_jitter_deg)
    rotation = _rotation_matrices(
        torch,
        len(batch),
        maximum_yaw_rad=float(radians),
        maximum_tilt_rad=float(radians * 0.5),
        device=batch.device,
    )
    # x is [batch, channel, time]. Rotate acceleration, gyro, and magnetic
    # vectors independently at every sample, preserving their physical groups.
    for start in (0, 3, 6):
        vectors = batch[:, start : start + 3, :].transpose(1, 2)
        batch[:, start : start + 3, :] = torch.bmm(vectors, rotation.transpose(1, 2)).transpose(1, 2)
    batch[:, :3, :] += torch.randn_like(batch[:, :3, :]) * 0.025
    batch[:, 3:6, :] += torch.randn_like(batch[:, 3:6, :]) * 0.004
    magnetic_norm = torch.linalg.vector_norm(batch[:, 6:9, :], dim=1, keepdim=True).clamp_min(1e-6)
    batch[:, 6:9, :] = batch[:, 6:9, :] / magnetic_norm
    return (batch - feature_mean) / feature_std


def train_multidrive_velocity_cnn(
    dataset: GroupedVelocityDataset,
    *,
    validation_groups: Iterable[str],
    test_groups: Iterable[str],
    epochs: int = 80,
    batch_size: int = 128,
    maximum_speed_mps: float = 45.0,
    maximum_orientation_jitter_deg: float = 12.0,
    early_stopping_patience: int = 8,
    seed: int = 19,
) -> tuple[Any, dict[str, np.ndarray], MultiDriveVelocityTrainingResult]:
    """Train against drive-disjoint validation/test recordings.

    No temporal neighbours from a held-out drive enter training. This is a
    stricter and more relevant check than random-window splits, which otherwise
    leak the same phone vibration, mount, road, and route into every split.
    """

    if (epochs < 1 or batch_size < 1 or maximum_speed_mps <= 0.0 or
            early_stopping_patience < 1):
        raise ValueError(
            "epochs, batch_size, maximum_speed_mps, and early_stopping_patience must be positive"
        )
    if not 0.0 <= maximum_orientation_jitter_deg <= 30.0:
        raise ValueError("maximum_orientation_jitter_deg must be in [0, 30]")
    groups = dataset.groups.astype(str)
    validation_names = tuple(sorted(set(validation_groups)))
    test_names = tuple(sorted(set(test_groups)))
    if not validation_names or not test_names:
        raise ValueError("validation_groups and test_groups must both be non-empty")
    if set(validation_names).intersection(test_names):
        raise ValueError("validation and test groups must not overlap")
    validation_idx = np.flatnonzero(np.isin(groups, validation_names))
    test_idx = np.flatnonzero(np.isin(groups, test_names))
    train_idx = np.flatnonzero(~np.isin(groups, validation_names + test_names))
    if min(len(train_idx), len(validation_idx), len(test_idx)) < 20:
        raise ValueError("each drive-disjoint split needs at least 20 valid windows")

    torch = _torch()
    torch.manual_seed(seed)
    torch.set_num_threads(min(4, torch.get_num_threads()))
    train_features = dataset.features[train_idx]
    mean = train_features.mean(axis=(0, 1), keepdims=True).astype(np.float32)
    std = np.maximum(train_features.std(axis=(0, 1), keepdims=True), 1e-4).astype(np.float32)
    normalized = ((dataset.features - mean) / std).transpose(0, 2, 1).astype(np.float32)
    target = dataset.speed_mps.astype(np.float32)
    if not np.isfinite(normalized).all() or not np.isfinite(target).all():
        raise ValueError("multi-drive dataset contains non-finite windows or labels")
    if target.min() < 0.0 or target.max() > maximum_speed_mps:
        raise ValueError(
            f"speed labels must be in [0, {maximum_speed_mps:.1f}] m/s; got "
            f"[{target.min():.2f}, {target.max():.2f}]"
        )
    target_mean = float(target[train_idx].mean())
    target_std = max(float(target[train_idx].std()), 1e-4)
    normalized_target = (target - target_mean) / target_std
    Model = _model_class()
    model = Model(
        channels=normalized.shape[1],
        target_mean=target_mean,
        target_std=target_std,
        maximum_speed_mps=maximum_speed_mps,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1.5e-3, weight_decay=2e-4)
    loss_fn = torch.nn.SmoothL1Loss()
    train_tensor = torch.from_numpy(normalized[train_idx])
    train_target = torch.from_numpy(normalized_target[train_idx])
    validation_tensor = torch.from_numpy(normalized[validation_idx])
    feature_mean = torch.from_numpy(mean.reshape(1, 9, 1))
    feature_std = torch.from_numpy(std.reshape(1, 9, 1))
    # Avoid learning only the dominant cruising-speed range. Equal-width bins
    # give starts/stops and low-speed traffic meaningful influence.
    bins = np.clip((target[train_idx] / 3.0).astype(int), 0, 14)
    counts = np.bincount(bins, minlength=15)
    weights = torch.from_numpy((1.0 / np.maximum(counts[bins], 1)).astype(np.float32))
    generator = torch.Generator().manual_seed(seed)
    best_validation_mae = float("inf")
    best_state: dict[str, Any] | None = None
    selected_epoch = 0
    trained_epochs = 0
    stale_epochs = 0
    for epoch in range(1, epochs + 1):
        order = torch.multinomial(weights, len(train_idx), replacement=True, generator=generator)
        for batch in order.split(batch_size):
            values = _augment_vehicle_frame(
                torch,
                train_tensor[batch],
                feature_mean=feature_mean,
                feature_std=feature_std,
                maximum_orientation_jitter_deg=maximum_orientation_jitter_deg,
            )
            prediction = model(values)
            loss = loss_fn(prediction, train_target[batch])
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
        model.eval()
        with torch.no_grad():
            validation_prediction = model(validation_tensor).cpu().numpy()
        validation_mae = _mae(
            validation_prediction * target_std + target_mean,
            target[validation_idx],
        )
        trained_epochs = epoch
        if validation_mae < best_validation_mae - 1e-7:
            best_validation_mae = validation_mae
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in model.state_dict().items()
            }
            selected_epoch = epoch
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= early_stopping_patience:
                break
        model.train()
    if best_state is None:  # pragma: no cover - one epoch always creates a checkpoint
        raise RuntimeError("multi-drive training did not produce a validation checkpoint")
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        normalized_prediction = model(torch.from_numpy(normalized)).cpu().numpy()
    prediction = normalized_prediction * target_std + target_mean
    maximum_prediction = float(np.max(prediction))
    minimum_prediction = float(np.min(prediction))
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    result = MultiDriveVelocityTrainingResult(
        train_windows=len(train_idx),
        validation_windows=len(validation_idx),
        test_windows=len(test_idx),
        train_groups=tuple(sorted(set(groups[train_idx]))),
        validation_groups=validation_names,
        test_groups=test_names,
        epochs=trained_epochs,
        selected_epoch=selected_epoch,
        parameter_count=parameter_count,
        validation_mae_mps=_mae(prediction[validation_idx], target[validation_idx]),
        test_mae_mps=_mae(prediction[test_idx], target[test_idx]),
        test_rmse_mps=float(np.sqrt(np.mean(np.square(prediction[test_idx] - target[test_idx])))),
        mean_speed_baseline_mae_mps=_mae(
            np.full(len(test_idx), target_mean, dtype=np.float32), target[test_idx]
        ),
        maximum_predicted_speed_mps=maximum_prediction,
        output_bounds_verified=minimum_prediction >= -1e-5
        and maximum_prediction <= maximum_speed_mps + 1e-5,
        orientation_jitter_degrees=maximum_orientation_jitter_deg,
    )
    normalization = {
        "feature_mean": mean.reshape(-1),
        "feature_std": std.reshape(-1),
        "target_mean": np.asarray(target_mean, dtype=np.float32),
        "target_std": np.asarray(target_std, dtype=np.float32),
        "sample_rate_hz": np.asarray(dataset.sample_rate_hz, dtype=np.float32),
        "maximum_speed_mps": np.asarray(maximum_speed_mps, dtype=np.float32),
    }
    return model, normalization, result
