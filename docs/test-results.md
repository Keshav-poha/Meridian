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

Dataset: IO-VNBD `M (Driver B)` subset, 5,000 aligned source rows, 10 Hz replay, 60-second GNSS blackout from 430 s to 490 s. This is a **legacy static-calibration benchmark**. Its model/normalizer, calibration inputs, and source files are hash-bound in [`metrics.json`](benchmarks/iovnbd-driver-b-stage12/metrics.json), but the current v2 artifact deliberately refuses this feature-space mismatch.

| Dataset / window | Method | Distance | Endpoint error | Drift | Position update rate | Result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| IO-VNBD Driver B / 60 s | Classical NHC | 689.81 m | 440.53 m | 63.86% | 10 Hz | Baseline |
| IO-VNBD Driver B / 60 s | Learned-speed NHC | 689.81 m | 54.71 m | 7.93% | 10 Hz | Passes `<10%` offline target |
| IO-VNBD Driver B / 60 s | Guarded masked-GNSS replay | 689.81 m | 54.71 m | 7.93% | 10 Hz | Safe learned-NHC fallback |
| IO-VNBD Driver B / 10 s | Any method | — | — | — | — | Not yet separately evaluated |
| IO-VNBD Driver B / 30 s | Any method | — | — | — | — | Not yet separately evaluated |
| Physical fixed-mount drive / 10, 30, 60 s | Any method | — | — | — | — | Not yet measured |

The guarded replay did not apply a residual correction: pre-outage speed correlation was 0.111 and yaw correlation was 0.007, so both were rejected. It therefore reports the learned NHC result rather than claiming an unsupported fusion improvement.

## Before/after comparison

| Comparison | Earlier value | Current value | Evidence |
| --- | ---: | ---: | --- |
| Classical NHC endpoint drift → learned-speed NHC endpoint drift | 63.86% | 7.93% | Same held-out Driver B blackout in [`metrics.csv`](benchmarks/iovnbd-driver-b-stage12/metrics.csv) |
| Classical implied-speed MAE → TinyVelocityCNN test MAE | 9.06 m/s | 2.41 m/s | Stage 5 provenance referenced by [`metrics.json`](benchmarks/iovnbd-driver-b-stage12/metrics.json) |

The legacy global-velocity model result is not a reproducible artifact in this repository, so it is not included as a numerical comparison.

## Map-matching safety replay

| Dataset / method | Accepted points | Trajectory RMSE before → after | Endpoint policy |
| --- | ---: | ---: | --- |
| Corrected offline hold-out / fail-closed OSM HMM | 282 / 600 (47%) | 31.73 m → 31.64 m | Final snap rejected; raw endpoint retained |

The small accuracy change is expected: the matcher rejects ambiguous or distant snaps rather than forcing every DR point onto a road. See [`docs/safety/map-matching.md`](safety/map-matching.md).

## Runtime and package checks

| Target | Command / method | Observed result | Target status |
| --- | --- | --- | --- |
| ML pipeline | `python -m unittest discover -s ml/tests -v` | 20 passed | Passes unit coverage |
| Edge runtime | `python -m unittest discover -s edge/python/tests -v` | 6 passed | Passes unit coverage |
| Flutter analysis | `flutter analyze` | No issues | Passes static analysis |
| Flutter tests | `flutter test` | 19 passed | Passes unit/widget coverage |
| Android debug package | `flutter build apk --debug` | Built successfully; 213,712,201 bytes | Debug package only |
| Mobile update rate | 100 ms runtime ticker | Configured for 10 Hz | **Physical measurement pending** |
| Edge velocity runtime | ONNX Runtime profile while fed a 200 Hz stream | 2,763 inferences/s | Exceeds feed rate for velocity reference; full edge navigation engine pending |

The mobile GNSS bootstrap change is covered by analysis, tests, and debug APK compilation. It still needs a final connected-phone check for a real coarse bootstrap fix, an aid-quality fix, and the transition to GNSS-aided state.

## Required plot and metrics artifacts

| Artifact | File | Status |
| --- | --- | --- |
| Required IO-VNBD position plot | [`position_plot.svg`](benchmarks/iovnbd-driver-b-stage12/position_plot.svg) | Tracked |
| Metrics table | [`metrics.csv`](benchmarks/iovnbd-driver-b-stage12/metrics.csv) | Tracked |
| Machine-readable provenance and metrics | [`metrics.json`](benchmarks/iovnbd-driver-b-stage12/metrics.json) | Tracked |
| Sample trajectory | [`trajectory.csv`](benchmarks/iovnbd-driver-b-stage12/trajectory.csv) | Tracked |

## Physical-validation boundary

No fixed-mount moving-car blackout has yet been measured. The field test must verify Android high-accuracy location acquisition, mount calibration, 10 Hz mobile update rate, 10/30/60-second blackout drift, and GNSS-loss/reacquisition latency. The current Flutter location layer does not expose satellite/provider provenance, so this cannot yet be reported as satellite-verified GNSS. Follow [`docs/validation/real-world-validation.md`](validation/real-world-validation.md), record a Developer Mode trip, and score it with `ml/scripts/evaluate_live_drive.py`.

The current Android build emits a non-fatal upstream `sensors_plus` Kotlin Gradle Plugin migration warning. It does not block the debug build, but should be addressed when a compatible plugin update is available.

## Reproduction

```powershell
$env:PYTHONPATH = 'ml/src;edge/python/src'
python -m unittest discover -s ml/tests -v
python -m unittest discover -s edge/python/tests -v
python ml/scripts/stage5_train_robust_velocity.py --epochs 20
python ml/scripts/stage10_export.py --artifact-dir ml/artifacts/velocity_cnn_robust --training-metrics ml/reports/stage5_robust/metrics.json

cd mobile
flutter analyze
flutter test
flutter build apk --debug
```

The Stage 12 command remains available only for the checked-in legacy model;
the current artifact stops before inference rather than publish an invalid
comparison.
