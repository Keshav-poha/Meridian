# Technical approach

This document describes the methods that are implemented today and separates them from work that remains experimental or offline-only. The common feature and model contract is [`shared/config/feature_spec.json`](../shared/config/feature_spec.json).

## Data synchronization and input contract

The ML pipeline loads paired IO-VNBD smartphone and vehicle CSV files, fits the phone-to-vehicle clock offset, and continuously interpolates onto a 10 Hz timeline. The current tracked Driver B replay uses a −3.9 s offset (`vehicle time = phone time − 3.9 s`). An interpolation gap over 0.25 s invalidates a window instead of being silently bridged.

The model contract is a 2-second window with 20 samples and nine channels:

```text
[linear acceleration forward/right/down,
 gyro forward/right/down,
 normalized magnetic direction forward/right/down]
```

The training split alone provides channel-wise z-score statistics. Sliding-window train/validation/test partitions are temporal, with a 1.8-second purge around boundaries to avoid shared raw IMU samples crossing a split.

## Mount calibration

Implementation: [`ml/src/idr_ml/calibration.py`](../ml/src/idr_ml/calibration.py) and the live preprocessor in [`mobile/lib/services/imu_preprocessor.dart`](../mobile/lib/services/imu_preprocessor.dart).

Static calibration selects low-speed samples with plausible gravity magnitude and solves a shortest rotation from measured phone gravity to vehicle down. This gives roll and pitch but cannot identify yaw. Dynamic calibration selects reliable turning segments and fits the remaining yaw by aligning gravity-compensated phone acceleration with vehicle kinematics:

```math
a_vehicle = R_body_to_vehicle (a_phone - g_phone)

a_reference = [d(speed)/dt, speed × yaw_rate]
```

The yaw offset is the weighted 2-D alignment that best maps the first vector to the second. Compass heading is retained as a diagnostic, not the yaw source, because vehicle interiors can disturb it. The live implementation requires sufficient duration, GNSS course quality, turning evidence, and mount-integrity checks before declaring calibration usable.

## IMU preprocessing and motion safeguards

Raw acceleration is gravity-compensated and rotated into the vehicle frame. Gyroscope samples update attitude over time; magnetometer vectors are normalized before entering the model. The live runtime uses a complementary attitude/preprocessing path rather than treating the phone's `z` axis as vehicle yaw.

[`mobile/lib/services/motion_gate.dart`](../mobile/lib/services/motion_gate.dart) applies stationary, shock-cooldown, out-of-distribution, freshness, mount, and GNSS-confirmed-motion gates. A rejected speed is held at zero; it is never clipped to a seemingly believable value. Current negative-motion tooling prepares labelled parked/idle, bump, handheld-shake, and mount-shift field logs, but the repository does not yet contain multi-device labelled negative data or a retrained classifier.

## Velocity model

Implementation: [`ml/src/idr_ml/velocity_model.py`](../ml/src/idr_ml/velocity_model.py).

`TinyVelocityCNN` is a 961-parameter 1-D CNN:

```text
9 × 20 IMU window
  → Conv1d(9, 16, kernel 5) + ReLU
  → grouped Conv1d(16, 16, kernel 3) + ReLU
  → temporal average
  → Linear(16, 1) + sigmoid-bounded forward-speed prior
```

It emits a scalar forward-speed prior in m/s, not global east/north velocity,
and is structurally bounded to 0–45 m/s. The v2 corpus uses independent
IO-VNBD Driver A recordings and a quality-audited STRIDE phone session with
recording-disjoint validation/evaluation. The raw IMU-only prior must not be
treated as a speedometer: in steady motion absolute speed is not observable
from accelerometer/gyro data alone. The live state starts from the latest
measured GNSS speed, integrates vehicle-frame forward acceleration, and uses
only a bounded CNN *change* as a residual; a persistent model prediction cannot
pull navigation speed toward itself. The compact model is exported as TFLite
and ONNX with parity checks and hash-bound manifests. Exact corpus and current
metrics are in [the v2 training record](model-training-v2.md).

## Dead reckoning and non-holonomic constraint

Implementation: [`ml/src/idr_ml/dead_reckoning.py`](../ml/src/idr_ml/dead_reckoning.py).

The integrator starts from a GNSS-aided state at the blackout boundary. It uses calibrated vehicle-frame yaw rate and either classical forward acceleration or learned scalar speed. The NHC explicitly sets lateral and vertical vehicle velocity to zero:

```math
heading[k] = heading[k-1] + (gyro_z[k] - bias_z) Δt
distance[k] = 0.5 (speed[k-1] + speed[k]) Δt
east[k]  = east[k-1]  + distance[k] sin(heading[k])
north[k] = north[k-1] + distance[k] cos(heading[k])
v_vehicle = [forward_speed, 0, 0]
```

This prevents bumps or mount shake from being treated as side-slip or vertical
travel. The tracked 60-second replay result (54.71 m over 689.81 m, 7.93%
drift) belongs to the legacy static-calibration artifact; it is retained as a
historical position plot and not asserted for the v2 model.

## Map matching

Implementation: [`ml/src/idr_ml/map_matching.py`](../ml/src/idr_ml/map_matching.py); policy: [`docs/safety/map-matching.md`](safety/map-matching.md).

The offline matcher constructs road candidates, scores them with a hidden-Markov/Viterbi path, and then applies a fail-closed gate. A point is accepted only when it is within 20 m of an eligible road, is at least 8 m clearer than a different nearby way, and—when heading/speed are reliable—agrees within 45 degrees. Rejected points remain raw DR coordinates and carry a rejection reason; they are never snapped to a plausible but wrong road.

On the tracked offline replay, 282 of 600 points were safely accepted and trajectory RMSE changed from 31.73 m to 31.64 m. A causal, directed matcher is not yet deployed on mobile or edge.

## GNSS-assisted fusion and switching

Implementation: [`ml/src/idr_ml/fusion.py`](../ml/src/idr_ml/fusion.py), [`ml/src/idr_ml/mode_switch.py`](../ml/src/idr_ml/mode_switch.py), and [`mobile/lib/services/live_idr_engine.dart`](../mobile/lib/services/live_idr_engine.dart).

The current offline fusion is an adaptive residual replay, not an Unscented Kalman Filter. It fits speed/yaw residuals only from pre-outage GNSS-aided history, checks correlation, and withholds the correction when the fit is weak. In the tracked evaluation, both residual corrections were withheld because their correlations were too weak; the output safely falls back to learned NHC instead of claiming a fusion gain.

The mobile runtime uses an acquiring state, GNSS-aided state, dead reckoning,
and a 500 ms reacquisition blend. It requires a current good fix after an
outage before blending back, preventing a cached pre-loss fix from causing a
jump. Flutter's Android `bestForNavigation` stream is the primary source and a
native raw-GPS stream is supplemental. A valid current fix up to 100 m
accuracy maintains navigation availability, while the tighter 25 m gate is
reserved for aiding/calibration. Receipt heartbeats plus physical fix age avoid
declaring loss just because Android repeats a stationary timestamp; backwards
timestamps and implausible jumps are still rejected.

## Mobile and edge deployment

The Flutter app loads `mobile/assets/models/velocity_cnn.tflite` through
`tflite_flutter`. Its 10 Hz ticker publishes only real sensor/location data and
exposes confidence and live diagnostics in Developer Mode, including when no
valid map coordinate exists. Android's current Flutter location API supplies a
high-accuracy location observation but does not expose a trustworthy
satellite/provider provenance field to Dart. Therefore physical results must
be described as **Android high-accuracy location aided**, not
satellite-verified GNSS, until a native GNSS-provider/satellite-status bridge
is added and validated. The Python edge reference in [`edge/python`](../edge/python)
validates input frames, resamples 100/200 Hz input into the shared 10 Hz model
window, and runs the ONNX model. The current export benchmark achieved about
2,763 velocity inferences/s while fed a 200 Hz stream; it is not yet a complete
high-rate edge INS/fusion/map-matching engine.

## Validation boundary

The reproducible evidence is an offline IO-VNBD held-out replay. A real fixed-mount car drive is required to validate device update rate, transition latency, cross-phone/mount robustness, and real GNSS blackout drift. The field protocol and evaluator are in [`docs/validation/real-world-validation.md`](validation/real-world-validation.md).
