# Stage 12 — reproducible IO-VNBD evaluation

This folder is MERIDIAN's current offline position-inference deliverable. It
evaluates the tracked, portable ONNX model rather than an ignored local
checkpoint. It is evidence for one held-out IO-VNBD replay, not a claim that
the mobile app is ready for unsupervised road use.

## Reproduction

With the public IO-VNBD `M (Driver B)` phone/vehicle CSV pair in
`ml/data/raw/iovnbd_m/`, run the complete provenance-preserving sequence:

```powershell
$env:PYTHONPATH = 'ml/.vendor;ml/src;edge/python/src'
py -3.13 ml/scripts/stage3_calibrate.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv --max-rows 5000 --calibration-end-seconds 430
py -3.13 ml/scripts/stage5_train_velocity.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv --max-rows 5000
py -3.13 ml/scripts/stage10_export.py
py -3.13 ml/scripts/stage12_evaluate.py
```

The pipeline rejects a stale/mixed export, a mismatched clock offset, a
calibration without an exclusive end boundary, or a calibration extending
into the held-out blackout. It also refuses windows that interpolate across a
source gap and purges 1.8 seconds of overlapping sliding windows around each
training split boundary.

## Current held-out replay

The replay aligns the first 5,000 source rows at 10 Hz using a fitted phone to
vehicle clock offset of **−3.9 s** (`vehicle time = phone time − 3.9 s`). The
phone GPS-speed column is treated as metres/second after checking it against
the vehicle trace. Mount calibration uses only samples before 430 s; the GNSS
blackout is 430–490 s.

The last GNSS-proxy speed/course sample at the loss boundary initializes the
state. After that, all `gt_*` columns are dropped before propagation and are
used only to score and plot the result.

| Method | Travelled | Endpoint error | Drift | Position update rate |
| --- | ---: | ---: | ---: | ---: |
| Classical NHC | 689.81 m | 440.53 m | 63.86% | 10 Hz |
| Learned-speed NHC | 689.81 m | 54.71 m | 7.93% | 10 Hz |
| Masked-GNSS adaptive replay | 689.81 m | 54.71 m | **7.93%** | 10 Hz |

This passes the `<10%` offline blackout target. The adaptive replay's
pre-outage speed and yaw correlations were only 0.111 and 0.007,
respectively, so its residual correction was deliberately withheld; it fell
back to the learned NHC estimate instead of applying an untrusted scale.
That is safer, but it is **not** evidence that the current replay fusion
improves every segment or that a live UKF has been implemented.

Files:

- `position_plot.svg` — ground truth, learned DR, and guarded replay path in
  a common local-ENU coordinate frame.
- `metrics.csv` and `metrics.json` — machine-readable metrics, model hashes,
  source hashes, alignment evidence, and fusion-guard state.
- `trajectory.csv` — sample-by-sample coordinates for independent plotting.
