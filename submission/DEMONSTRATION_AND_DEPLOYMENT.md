# MERIDIAN Demonstration and Deployment

## Demonstration components

| Component | Demonstration role |
| --- | --- |
| Android application | Shows GNSS-aided navigation, dead-reckoning state, recovery state, live telemetry, confidence, and trip recording. |
| TFLite model | Runs local velocity inference from the smartphone IMU window. |
| ONNX edge reference | Shows the portable external-IMU inference path with the same feature contract. |
| Stage 12 evaluator | Regenerates the IO-VNBD position plot, speed plot, drift plot, metrics, and trajectory. |

## Android preparation

1. Install the release APK produced from the `mobile/` project, or build it with the command below.
2. Grant location permission when prompted.
3. Keep the phone mounted securely before attempting calibration or dead reckoning.
4. Start outdoors with a recent quality GNSS fix so the application can establish an aided state.
5. Confirm that the map displays `© OpenStreetMap contributors` and that sensor data appears in Developer Mode.

```powershell
cd mobile
flutter pub get
flutter analyze
flutter test
flutter build apk --release
```

The Android manifest declares `HIGH_SAMPLING_RATE_SENSORS` to prevent Android 12 and later from applying the default sensor-rate throttle. The release APK is non-debuggable. Before public distribution, the team should configure a team-owned release keystore rather than use a local development signing key.

## Suggested app demonstration

1. Open MERIDIAN and allow GNSS acquisition. Show the navigation screen, current accuracy, speed, heading, and position marker.
2. Open Developer Mode and show the live accelerometer, gyroscope, GNSS, confidence, and calibration indicators.
3. With a secure fixed mount and a quality GNSS period, allow the application to reach a usable aided state.
4. Use the Developer Mode GNSS-outage control to demonstrate transition to dead reckoning. The physical GNSS reference remains comparison-only and does not feed the displayed prediction while aiding is disabled.
5. Re-enable aiding and show the 500 ms recovery blend after a fresh quality fix is accepted.
6. Start and stop a trip recording to show the field-validation log path.

Do not interact with the application while driving. A second person should operate the test controls, or the demonstration should be performed while stationary or through the controlled simulator.

## Edge reference demonstration

The Python reference accepts an external IMU frame with calibrated vehicle axes and validates it before model input. It can be tested from the repository root:

```powershell
python -m pip install -e edge/python
$env:PYTHONPATH = "$PWD\edge\python\src"
python -m unittest discover -s edge/python/tests -v
```

For a 200 Hz external stream, create the runtime with the expected input rate. It resamples the newest two seconds into the shared 10 Hz model window and declines to emit a speed estimate when timestamps or sensor continuity fail the input-integrity checks. The edge reference currently covers portable velocity inference, not the complete high-rate INS, fusion, and map-matching stack.

## Evaluation demonstration

```powershell
$env:PYTHONPATH = "ml/src;edge/python/src"
py -3.13 ml/scripts/stage12_evaluate.py
```

The evaluator loads `shared/models/velocity_cnn.onnx`, validates the manifest and hashes, and writes position, speed, drift, metrics, and trajectory artifacts. Review the outputs in [the benchmark directory](../docs/benchmarks/README.md).

## Fixed-mount field test

The controlled field test is separate from the offline benchmark. Mount the phone, collect an aided segment, create one 10, 30, or 60-second controlled outage, recover GNSS, save the trip log, and score it with:

```powershell
py -3.13 ml/scripts/evaluate_live_drive.py <trip-log.jsonl> --windows 10,30,60 --output-dir ml/reports/live_drive
```

Follow the [physical-drive validation guide](../docs/validation/real-world-validation.md) for safe collection rules, required negative-motion captures, and interpretation of the results.
