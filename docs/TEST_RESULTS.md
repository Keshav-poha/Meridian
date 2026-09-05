# Test results

This page records the latest local verification of the committed repository.
Offline metrics are clearly separated from the physical-device smoke check.

## 5 September 2026 — corrected pipeline and runtime pass

| Target | Command | Result |
| --- | --- | --- |
| Shared ML pipeline | `py -3.13 -m unittest discover -s ml/tests -v` | 17 passed, including clock alignment, missing-window rejection, split purge, map safety, weak-residual fallback, sensor-only blackout propagation, and live-drive evaluator coverage. |
| Edge preprocessing/runtime | `py -3.13 -m unittest discover -s edge/python/tests -v` | 6 passed: input validation, timestamp replay, 200 Hz resampling, and gap reset. |
| Flutter static analysis | `flutter analyze` | No issues. |
| Flutter test suite | `flutter test` | 10 passed: mount evidence, shock/motion gates, recovery freshness gate, controller, and recorder behavior. |
| Android package | `flutter build apk --debug` | Built successfully; 213,712,201-byte APK. |
| Android install/startup | `flutter install --debug -d 001966572000417` and `flutter run --debug --no-resident -d 001966572000417` | Installed and launched on the connected Android 16 phone; no Flutter/TFLite exception in the inspected startup log. |
| Portable model export | `py -3.13 ml/scripts/stage10_export.py` | Hash-bound 5,387-byte ONNX and 10,276-byte TFLite model; ONNX/TFLite parity ≤7.76e−8; hardened Python reference produced 3,248 inferences/s while fed a 200 Hz stream. |
| Offline map safety | `py -3.13 ml/scripts/stage7_map_match.py` | 282/600 points accepted under distance/ambiguity/heading rules; RMSE 31.73→31.64 m. Endpoint stayed raw because the final snap was rejected. |
| IO-VNBD held-out replay | `py -3.13 ml/scripts/stage12_evaluate.py` | **7.93%** endpoint drift (54.71 m / 689.81 m) at 10 Hz; passes the `<10%` offline requirement. |

The Stage 12 metric uses a corrected `−3.9 s` phone-to-vehicle clock fit,
time-bounded pre-blackout calibration, gap-safe windows, a 1.8-second split
purge, and the tracked portable ONNX artifact. Its adaptive residual was
withheld because the pre-outage speed/yaw correlations were too weak; it is a
safe learned-NHC fallback, not a proof that a live UKF improves this segment.
See [Stage 12 evaluation](evaluation/stage12/README.md).

## Physical UI/device check

- Three-button Android navigation was visually verified on the connected
  phone: all MERIDIAN bottom tabs sit above the system button strip.
- Gesture mode was switched at OS level and then restored to the original
  three-button state. The app visual capture was interrupted by the locked
  launcher view, so gesture-mode layout still needs one manual unlocked-phone
  check.
- No fixed-mount, moving-car blackout has been measured yet. The deployed
  phone build is a sensor/model/startup smoke check, not a real-world drift
  validation.

The build logs include a non-fatal upstream `sensors_plus` warning about its
Kotlin Gradle plugin migration. It does not block this build, but should be
addressed when the dependency publishes a compatible update.

## Reproduce locally

```powershell
$env:PYTHONPATH = 'ml/.vendor;ml/src;edge/python/src'
py -3.13 -m unittest discover -s ml/tests -v
py -3.13 -m unittest discover -s edge/python/tests -v
py -3.13 ml/scripts/stage3_calibrate.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv --max-rows 5000 --calibration-end-seconds 430
py -3.13 ml/scripts/stage5_train_velocity.py --smartphone ml/data/raw/iovnbd_m/S-M.csv --vehicle ml/data/raw/iovnbd_m/V-M.csv --max-rows 5000
py -3.13 ml/scripts/stage10_export.py
py -3.13 ml/scripts/stage12_evaluate.py

& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' analyze
& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' test
& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' build apk --debug
```

For field validation, record a fixed-mount drive in Developer Mode and run
`ml/scripts/evaluate_live_drive.py`; the protocol is in
`docs/REAL_WORLD_VALIDATION.md`.
