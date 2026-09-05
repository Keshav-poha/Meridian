# ML and replay pipeline

`idr_ml` owns IO-VNBD ingestion, timestamp synchronization, mount calibration, velocity training, offline replay, model export, and reproducible evaluation. Shared input/output contracts live in [`../shared`](../shared).

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

## Reproducible Driver B sequence

Fetch the public `M (Driver B)` smartphone/vehicle pair, then run the stages below from the repository root:

```powershell
python ml/scripts/fetch_iovnbd_subset.py
python ml/scripts/stage2_ingest.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv
python ml/scripts/stage3_calibrate.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv --max-rows 5000 --calibration-end-seconds 430
python ml/scripts/stage4_baseline_dr.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv
python ml/scripts/stage5_train_velocity.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv --max-rows 5000
python ml/scripts/stage6_learned_velocity_dr.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv
python ml/scripts/stage7_map_match.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv
python ml/scripts/stage8_fusion.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv
python ml/scripts/stage10_export.py
python ml/scripts/stage12_evaluate.py
```

Stage 10 needs TensorFlow in addition to `ml/requirements.txt` when TFLite export is required:

```powershell
python -m pip install tensorflow
```

Stage 12 evaluates the tracked portable ONNX artifact rather than a local checkpoint. It writes the held-out position plot, metrics, and trajectory to [`../docs/benchmarks/iovnbd-driver-b-stage12/`](../docs/benchmarks/iovnbd-driver-b-stage12/) and exits non-zero if the offline drift threshold cannot be evaluated or is missed.

## Field-drive evaluation

After recording a fixed-mount drive in Developer Mode, score the copied JSONL file with:

```powershell
python ml/scripts/evaluate_live_drive.py <trip-log.jsonl> --windows 10,30,60 --output-dir ml/reports/live_drive
```

See [`../docs/validation/real-world-validation.md`](../docs/validation/real-world-validation.md) for the collection protocol and known validation boundary.
