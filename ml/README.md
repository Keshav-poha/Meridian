# ML and replay pipeline

`idr_ml` owns IO-VNBD/STRIDE ingestion, timestamp synchronization, mount calibration, velocity training, offline replay, model export, and reproducible evaluation. Shared input/output contracts live in [`../shared`](../shared).

## Environment

From the repository root, use Python 3.10 or later:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r ml/requirements.txt
$env:PYTHONPATH = "$PWD\ml\src;$PWD\edge\python\src"
python -m idr_ml.validate_contract
```

Raw datasets, checkpoints, reports, and local package caches are intentionally ignored by Git.

## Current runtime-equivalent model

Download the public IO-VNBD recordings and the STRIDE driving data described in
[`../docs/model-training-v2.md`](../docs/model-training-v2.md), then run the
current training/export stages from the repository root:

```powershell
$env:PYTHONPATH = "$PWD\ml\src;$PWD\edge\python\src"
python ml/scripts/stage5_train_robust_velocity.py --epochs 20
python ml/scripts/stage10_export.py --artifact-dir ml/artifacts/velocity_cnn_robust --training-metrics ml/reports/stage5_robust/metrics.json
```

The Stage 5 model is a bounded velocity prior, not a standalone absolute-speed
replacement. At runtime its changes are fused into the GNSS-anchored inertial
state. The stage records a raw-prior baseline comparison but does not allow a
raw model value to reset navigation speed.

Stage 10 needs TensorFlow and ONNX in addition to `ml/requirements.txt` when TFLite/ONNX export is required:

```powershell
python -m pip install tensorflow onnx
```

## Legacy Driver B position replay

The tracked Stage 12 position plot uses the earlier static-calibration model.
The current artifact uses mobile-equivalent GNSS-kinematic yaw and intentionally
rejects that replay rather than run with mismatched preprocessing. See
[`../docs/model-training-v2.md`](../docs/model-training-v2.md) for the current
artifact and use a fixed-mount field drive for its position validation.

## Field-drive evaluation

After recording a fixed-mount drive in Developer Mode, score the copied JSONL file with:

```powershell
python ml/scripts/evaluate_live_drive.py <trip-log.jsonl> --windows 10,30,60 --output-dir ml/reports/live_drive
```

See [`../docs/validation/real-world-validation.md`](../docs/validation/real-world-validation.md) for the collection protocol and known validation boundary.
