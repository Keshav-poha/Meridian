# Test results

This report separates reproducible offline evidence from device and road validation. Results below were rechecked locally on 5 September 2026 unless stated otherwise.

## Current runtime-equivalent artifact

The bundled mobile/edge model was retrained with the same gravity-plus-GNSS
kinematic yaw preprocessing used by the mobile app. It has a structural 0–45
m/s output bound, and the live INS filter refuses to use its absolute value as
a speed reset: only a small, rate-limited change can correct the GNSS-anchored
accelerometer integration. ONNX/TFLite export parity was verified at 1.19e-7
and 2.38e-7 normalized units respectively; the edge reference processed about
2,763 velocity inferences/s while receiving a 200 Hz stream. See
[the v2 training record](model-training-v2.md) for the corpus, split, and
recording-disjoint raw-prior result.

## Held-out IO-VNBD blackout benchmark

Dataset: recording-disjoint IO-VNBD Driver A S3c, 10 Hz replay, 59.9-second GNSS blackout beginning at 1,200.1 s. The evaluator loads the current shared ONNX artifact, calibrates using samples before 300 s only, and excludes reference labels from blackout inference. Model, normalization, source hashes, calibration evidence, and the acceptance result are recorded in [`metrics.json`](benchmarks/iovnbd-driver-a-s3c-stage12/metrics.json).

| Dataset / window | Method | Distance | Endpoint error | Drift | Position update rate | Result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| IO-VNBD Driver A S3c / 59.9 s | Forward-acceleration INS | 1,867.62 m | 96.34 m | 5.16% | 10 Hz | Baseline |
| IO-VNBD Driver A S3c / 59.9 s | AI-assisted GNSS-anchored INS | 1,867.62 m | 94.88 m | **5.08%** | 10 Hz | Passes `<10%` offline target |
| IO-VNBD Driver A S3c / 10 s | Any method | — | — | — | — | Not yet separately evaluated |
| IO-VNBD Driver A S3c / 30 s | Any method | — | — | — | — | Not yet separately evaluated |
| Physical fixed-mount drive / 10, 30, 60 s | Any method | — | — | — | — | Not yet measured |

The AI-assisted state applies the same bounded learned speed-rate correction as the mobile runtime. Its 5.08% endpoint drift is below the SIH ceiling, while physical field results remain unmeasured.

## Before/after comparison

| Comparison | Earlier value | Current value | Evidence |
| --- | ---: | ---: | --- |
| Forward-acceleration INS endpoint drift → AI-assisted GNSS-anchored INS endpoint drift | 5.16% | 5.08% | Same held-out Driver A S3c blackout in [`metrics.csv`](benchmarks/iovnbd-driver-a-s3c-stage12/metrics.csv) |
| Evaluation mean-speed baseline MAE → TinyVelocityCNN raw-prior MAE | 6.33 m/s | 6.40 m/s | Recording-disjoint raw-prior result in [`model-training-v2.md`](model-training-v2.md) |

The raw IMU-only prior is not presented as a standalone speedometer; the navigation result uses GNSS anchoring and a bounded rate correction.

## Map-matching safety replay

| Dataset / method | Accepted points | Trajectory RMSE before → after | Endpoint policy |
| --- | ---: | ---: | --- |
| Corrected offline hold-out / fail-closed OSM HMM | 282 / 600 (47%) | 31.73 m → 31.64 m | Final snap rejected; raw endpoint retained |

The small accuracy change is expected: the matcher rejects ambiguous or distant snaps rather than forcing every DR point onto a road. See [`docs/safety/map-matching.md`](safety/map-matching.md).

## Runtime and package checks

| Target | Command / method | Observed result | Target status |
| --- | --- | --- | --- |
| ML pipeline | `python -m unittest discover -s ml/tests -v` | 22 passed | Passes unit coverage |
| Edge runtime | `python -m unittest discover -s edge/python/tests -v` | 6 passed | Passes unit coverage |
| Flutter analysis | `flutter analyze` | No issues | Passes static analysis |
| Flutter tests | `flutter test` | 19 passed | Passes unit/widget coverage |
| Android debug package | `flutter build apk --debug` | Built successfully; 213,712,201 bytes | Debug package only |
| Mobile update rate | 100 ms runtime ticker | Configured for 10 Hz | **Physical measurement pending** |
| Edge velocity runtime | ONNX Runtime profile while fed a 200 Hz stream | 2,763 inferences/s | Exceeds feed rate for velocity reference; full edge navigation engine pending |

The mobile GNSS recovery change is covered by analysis, tests, and debug APK compilation. It still needs a final connected-phone check for stable high-accuracy aiding and the transition through a real GNSS outage.

## Required plot and metrics artifacts

| Artifact | File | Status |
| --- | --- | --- |
| Required IO-VNBD position plot | [`position_plot.svg`](benchmarks/iovnbd-driver-a-s3c-stage12/position_plot.svg) | Tracked |
| Required speed-tracking plot | [`speed_tracking_plot.svg`](benchmarks/iovnbd-driver-a-s3c-stage12/speed_tracking_plot.svg) | Tracked |
| Required drift-versus-distance plot | [`drift_vs_distance_plot.svg`](benchmarks/iovnbd-driver-a-s3c-stage12/drift_vs_distance_plot.svg) | Tracked |
| Metrics table | [`metrics.csv`](benchmarks/iovnbd-driver-a-s3c-stage12/metrics.csv) | Tracked |
| Machine-readable provenance and metrics | [`metrics.json`](benchmarks/iovnbd-driver-a-s3c-stage12/metrics.json) | Tracked |
| Sample trajectory | [`trajectory.csv`](benchmarks/iovnbd-driver-a-s3c-stage12/trajectory.csv) | Tracked |

## Physical-validation boundary

No fixed-mount moving-car blackout has yet been measured. The field test must verify Android high-accuracy location acquisition, mount calibration, 10 Hz mobile update rate, 10/30/60-second blackout drift, and GNSS-loss/reacquisition latency. Follow [`docs/validation/real-world-validation.md`](validation/real-world-validation.md), record a Developer Mode trip, and score it with `ml/scripts/evaluate_live_drive.py`.

The current Android build emits a non-fatal upstream `sensors_plus` Kotlin Gradle Plugin migration warning. It does not block the debug build, but should be addressed when a compatible plugin update is available.

## Reproduction

```powershell
$env:PYTHONPATH = 'ml/src;edge/python/src'
python -m unittest discover -s ml/tests -v
python -m unittest discover -s edge/python/tests -v
python ml/scripts/stage5_train_robust_velocity.py --epochs 20
python ml/scripts/stage10_export.py --artifact-dir ml/artifacts/velocity_cnn_robust --training-metrics ml/reports/stage5_robust/metrics.json
py -3.13 ml/scripts/fetch_iovnbd_subset.py --recording driver-a-s3c
py -3.13 ml/scripts/stage12_evaluate.py

cd mobile
flutter analyze
flutter test
flutter build apk --debug
```

Stage 12 evaluates the current deployable artifact and exits non-zero if its
AI-assisted drift meets or exceeds the 10% threshold.
