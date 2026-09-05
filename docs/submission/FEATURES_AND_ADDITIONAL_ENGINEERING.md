# MERIDIAN Features and Additional Engineering

## System features

### Vehicle independent phone alignment

MERIDIAN does not assume that the phone is mounted in a fixed portrait or landscape orientation. Static gravity alignment estimates pitch and roll. Dynamic turning segments then fit yaw against vehicle kinematics when GNSS course and mount evidence are sufficient. The resulting transform lets the rest of the system operate in forward, right, and down vehicle axes instead of raw phone axes.

### Gravity compensated inertial processing

The preprocessor removes gravity before velocity inference and navigation propagation. It rotates acceleration, gyroscope, and normalized magnetic direction into the vehicle frame. This avoids treating gravity or a handset's screen orientation as forward vehicle acceleration.

### Bounded IMU velocity prior

The 961-parameter TinyVelocityCNN consumes a two-second 9-channel IMU window and emits forward-speed information only. A sigmoid output enforces a 0 to 45 m/s deployment bound. The model does not serve as an absolute speedometer during steady motion. It contributes a bounded rate correction to a speed state that begins from recent GNSS aiding.

### Motion quality gates

The live runtime checks for stationary operation, shock and bump cooldowns, stale samples, weak mount calibration, out-of-distribution windows, and unarmed vehicle motion. A rejected condition produces an unavailable or zero-confidence result instead of a plausible-looking speed estimate.

### GNSS loss and recovery handling

The Android application distinguishes acquisition, GNSS-aided navigation, dead reckoning, and reacquisition. A current valid fix with up to 100 m accuracy can maintain navigation availability. Tighter 25 m accuracy is required for sensitive aiding and calibration. After a deficit, a new good fix is required before a 500 ms reacquisition blend begins.

### Non holonomic dead reckoning

The navigation model propagates forward motion and calibrated heading while forcing lateral and vertical vehicle velocity to zero. This reflects the normal road-vehicle constraint and reduces the effect of vibration, bumps, or isolated accelerometer spikes.

### Conservative road matching

The offline map matcher combines HMM/Viterbi road candidates with maximum-distance, ambiguity, and heading checks. It is designed to reject an uncertain snap rather than place a vehicle on a nearby but incorrect road. It remains an offline component until a causal mobile or edge integration is completed.

### Mobile and edge deployment

The Flutter app runs a TFLite model locally and exposes navigation and telemetry screens. The Python ONNX Runtime reference supports validated external IMU streams at 100 Hz or 200 Hz. Both use the same feature contract and normalization values.

## Engineering undertaken beyond the minimum problem statement

| Additional work | Why it was added | Evidence |
| --- | --- | --- |
| Timestamp synchronization and source-gap rejection | Paired phone and vehicle data can have clock offset or missing samples. The pipeline fits an offset and rejects interpolation across a gap longer than 0.25 s. | [IO-VNBD pipeline](../../ml/src/idr_ml/iovnbd.py) |
| Leakage-resistant evaluation split | Sliding-window splits purge overlapping IMU history around temporal boundaries and keep the evaluation recording separate from training and validation recordings. | [Training record](../model-training-v2.md) |
| Hash-bound model provenance | ONNX and TFLite exports include input order, normalization, source hashes, and model hashes so deployment and evaluation artifacts cannot be mixed silently. | [Model manifests](../../shared/models/) |
| Confidence and reason reporting | Each live prediction carries confidence and a reason so calibration, motion, and data-quality failures are visible to an evaluator. | [Velocity quality state](../../mobile/lib/services/velocity_model_quality.dart) |
| Developer telemetry and trip recording | The application records live sensor axes, GNSS observations, predicted positions, confidence, and manual outage transitions for field scoring. | [Trip recorder](../../mobile/lib/services/trip_recorder.dart) |
| Manual outage simulator and scoring tool | The simulator keeps physical GNSS reference data separate from the DR state, while the desktop evaluator computes endpoint error, distance, drift, rate, and switch latency. | [Field validation protocol](../validation/real-world-validation.md) |
| Negative-motion data preparation | Separate labels exist for parked or idle state, potholes or bumps, handheld shake, and mount shift. The tool refuses to create a complete training set when a class is missing. | [Preparation script](../../ml/scripts/prepare_motion_negatives.py) |
| External-IMU input integrity | The edge reference validates vector shape, finite values, magnetic normalization, timestamp monotonicity, and gaps before creating an inference window. | [Edge reference](../../edge/README.md) |
| Android sampling support and map attribution | The Android manifest requests high sampling rate sensor access, and the map displays the required OpenStreetMap attribution. | [Android manifest](../../mobile/android/app/src/main/AndroidManifest.xml), [mobile UI](../../mobile/lib/ui/meridian_shell.dart) |
| Automated checks | Unit tests cover the ML pipeline and edge reference. Flutter analysis, widget tests, Android build, and GitHub verification are used to detect regressions. | [Test results](../test-results.md), [CI workflow](../../.github/workflows/verify.yml) |

## Current boundaries

The additional safeguards are intended to prevent unsupported claims. The repository does not claim that the current model has been trained on a collected multi-device negative-motion corpus, that the offline map matcher is live in the mobile app, or that a finished high-rate FOG navigation system exists. Fixed-mount road validation is required before presenting the offline benchmark as real-world performance.
