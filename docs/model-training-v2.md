# Runtime-equivalent velocity model (v2)

This is the training record for the model bundled in `shared/models/` and
`mobile/assets/models/`. It replaces the earlier single-recording model that
could extrapolate an implausible absolute speed on a real phone.

## Contract

The model consumes a 2-second, 20-sample window of nine channels:

```text
[linear acceleration forward/right/down,
 gyro forward/right/down,
 normalized magnetic direction forward/right/down]
```

Pitch and roll are removed with the same complementary gravity estimator used
by the Flutter runtime. The phone-to-vehicle yaw is derived from reliable GNSS
speed/course changes, not compass yaw. This is important for dashboard mounts,
where magnetic distortion is common. A straight or insufficiently dynamic
drive deliberately leaves the mount uncalibrated instead of guessing a yaw.

The output layer is structurally bounded to 0–45 m/s. The mobile runtime does
not use that output as an absolute speed reset: it anchors speed to the last
measured GNSS value, integrates vehicle-frame forward acceleration, and admits
only a small, rate-bounded CNN change-of-velocity residual. Consequently, a
constant bad model prediction cannot accelerate the navigation state toward
45 m/s.

## Corpus and leakage controls

The training stage uses public data downloaded locally but not committed:

| Role | Recordings | Labels |
| --- | --- | --- |
| Training | IO-VNBD Driver A S1 and S3a; STRIDE `1. Aggressive` | IO-VNBD vehicle telemetry; STRIDE phone GNSS speed as secondary domain supervision |
| Validation | IO-VNBD Driver A S3b | vehicle telemetry |
| Evaluation | IO-VNBD Driver A S3c | vehicle telemetry |

Each recording is transformed independently, with no random-window split.
Long recordings are deterministically capped at 6,000 windows so one drive
cannot dominate the corpus. The model has 961 parameters and is trained with
balanced speed bins, physical residual-orientation augmentation, AdamW,
SmoothL1 loss, and validation checkpoint selection.

The IO-VNBD Driver B subset was audited but rejected for this v2 model because
its kinematic yaw agreement was 0.12, below the same 0.20 threshold required
by the mobile calibration path. Rejecting it avoids training on a transform the
app would refuse in production.

## Reproducible result

The selected checkpoint stopped after 10 epochs (best validation checkpoint:
epoch 2). It used 13,858 training windows, 993 validation windows, and 6,000
recording-disjoint evaluation windows.

| Measure | Result |
| --- | ---: |
| Validation absolute-speed MAE | 5.101 m/s |
| Recording-disjoint evaluation absolute-speed MAE | 6.400 m/s |
| Evaluation mean-speed baseline MAE | 6.330 m/s |
| Maximum evaluated model prediction | 27.811 m/s |
| Output contract | 0–45 m/s verified |
| ONNX parity error | 1.19e-7 normalized units |
| TFLite parity error | 2.38e-7 normalized units |

The standalone IMU-only absolute-speed prior did not beat the mean-speed
baseline on this small, recording-disjoint evaluation. That is an expected
observability limit during steady driving: acceleration and gyro alone cannot
determine absolute cruise speed. It is why MERIDIAN uses the model inside a
GNSS-anchored INS state rather than presenting its raw output as navigation
speed. These figures are not a real-road accuracy claim and do not establish
the SIH drift target.

## Reproduce

Install the documented IO-VNBD and STRIDE data locally, then run from the
repository root:

```powershell
$env:PYTHONPATH = "$PWD\ml\src;$PWD\edge\python\src"
python ml/scripts/stage5_train_robust_velocity.py --epochs 20
python ml/scripts/stage10_export.py --artifact-dir ml/artifacts/velocity_cnn_robust --training-metrics ml/reports/stage5_robust/metrics.json
```

The exporter checks numerical ONNX/TFLite parity and updates the shared edge
and mobile artifacts. Use `--require-prior-baseline-win` when an experiment is
specifically intended to qualify a raw IMU-only prior; normal deployment does
not relax the bounded GNSS-anchored state safeguards.
