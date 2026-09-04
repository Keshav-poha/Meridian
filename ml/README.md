# ML/replay pipeline

`idr_ml` owns offline ingestion, synchronization, calibration, classical dead reckoning, model training, and evaluation. Its public input/output definitions are in `../shared`.

```powershell
$idrPython = 'C:\\Users\\Keshav\\.cache\\meridian-runtimes\\meridian-primary-runtime\\dependencies\\python\\python.exe'
& $idrPython -m idr_ml.validate_contract
```

## Stage 2 replay command

Fetch the public synchronized `M (Driver B)` pair, then run a 5,000-row
reproducible subset through timestamp synchronization, fixed-window creation,
and a ground-truth/IMU SVG sanity plot:

```powershell
$idrPython = 'C:\\Users\\Keshav\\.cache\\meridian-runtimes\\meridian-primary-runtime\\dependencies\\python\\python.exe'
& $idrPython ml/scripts/fetch_iovnbd_subset.py
$env:PYTHONPATH = 'ml\\src'
& $idrPython ml/scripts/stage2_ingest.py `
  --smartphone ml/data/raw/iovnbd_m/S-M.csv `
  --vehicle ml/data/raw/iovnbd_m/V-M.csv
```

Generated raw data and reports remain beneath ignored folders in this directory.

## Stage 3 calibration command

```powershell
$env:PYTHONPATH = 'ml\\src'
& $idrPython ml/scripts/stage3_calibrate.py `
  --smartphone ml/data/raw/iovnbd_m/S-M.csv `
  --vehicle ml/data/raw/iovnbd_m/V-M.csv
```

## Stage 4 baseline DR command

```powershell
$env:PYTHONPATH = 'ml\\src'
& $idrPython ml/scripts/stage4_baseline_dr.py `
  --smartphone ml/data/raw/iovnbd_m/S-M.csv `
  --vehicle ml/data/raw/iovnbd_m/V-M.csv
```

## Stage 5 velocity-model command

Install the project-local CPU PyTorch dependency once, then train and compare
the temporal held-out split with the Stage 4 classical implied speed:

```powershell
& $idrPython -m pip install --target ml/.vendor torch --index-url https://download.pytorch.org/whl/cpu
$env:PYTHONPATH = 'ml/.vendor;ml/src'
& $idrPython ml/scripts/stage5_train_velocity.py `
  --smartphone ml/data/raw/iovnbd_m/S-M.csv `
  --vehicle ml/data/raw/iovnbd_m/V-M.csv
```

## Stage 6 learned-speed DR command

```powershell
$env:PYTHONPATH = 'ml/.vendor;ml/src'
& $idrPython ml/scripts/stage6_learned_velocity_dr.py `
  --smartphone ml/data/raw/iovnbd_m/S-M.csv `
  --vehicle ml/data/raw/iovnbd_m/V-M.csv
```

## Stage 7 OSM map-matching command

```powershell
$env:PYTHONPATH = 'ml/.vendor;ml/src'
& $idrPython ml/scripts/stage7_map_match.py `
  --smartphone ml/data/raw/iovnbd_m/S-M.csv `
  --vehicle ml/data/raw/iovnbd_m/V-M.csv
```

## Stage 8 adaptive fusion command

```powershell
$env:PYTHONPATH = 'ml/.vendor;ml/src'
& $idrPython ml/scripts/stage8_fusion.py `
  --smartphone ml/data/raw/iovnbd_m/S-M.csv `
  --vehicle ml/data/raw/iovnbd_m/V-M.csv
```

## Stage 10 portable export command

This exports the trained Stage 5 checkpoint to the versioned shared model
directory and copies the mobile TFLite asset. The exporter checks ONNX and
TFLite output against PyTorch, then profiles actual ONNX Runtime calls from a
synthetic 200 Hz FOG-grade IMU stream.

```powershell
py -3.13 -m pip install --target ml/.vendor torch tensorflow onnx onnxruntime
$env:PYTHONPATH = 'ml/.vendor;ml/src;edge/python/src'
py -3.13 ml/scripts/stage10_export.py
```

`shared/models/velocity_cnn.normalization.json` is the single preprocessing
artifact consumed by both targets. ONNX accepts `[batch, channel, time]`; the
TFLite asset accepts the equivalent `[batch, time, channel]` layout.
