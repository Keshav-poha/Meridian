# Test results

This page records the most recent local verification of the committed
repository. The GitHub Actions workflow in `.github/workflows/verify.yml`
repeats the unit and build checks on every push and pull request.

## 4 September 2026

| Target | Command | Result |
| --- | --- | --- |
| Shared ML pipeline | `py -3.13 -m unittest discover -s ml/tests -v` | 6 passed |
| Edge preprocessing | `py -3.13 -m unittest discover -s edge/python/tests -v` | 1 passed in 0.027 s |
| Flutter static analysis | `flutter analyze` | No issues (10.6 s) |
| Flutter test suite | `flutter test` | 6 passed |
| Android package | `flutter build apk --debug` | Built successfully; 173,619,646-byte APK |
| IO-VNBD held-out replay | `py -3.13 ml/scripts/stage12_evaluate.py` | 7.90% endpoint drift (53.50 m / 676.92 m) at 10 Hz; passes the <10% requirement |

The position plot, sample-level trajectory, and machine-readable metric report
are in [Stage 12 evaluation](evaluation/stage12/README.md).

## 5 September 2026 — live Android smoke test

| Check | Result |
| --- | --- |
| Device and permissions | Android 16 physical device; live accelerometer, gyroscope, magnetometer, and fused GNSS data reached Developer Mode. |
| GNSS request | High-accuracy request accepted at 1 second. The Android fused provider batched stationary callbacks at about 5 seconds, so the runtime uses an 8-second freshness watchdog to avoid false loss banners. |
| Raw-axis model diagnosis | The original artifact emitted a raw 7.46 m/s on a stationary phone because its raw magnetic field was far outside the training distribution. It has been replaced. |
| Orientation/gravity correction | The shipped model now consumes complementary-filter gravity-compensated acceleration, a dynamic GNSS-course mount transform, and normalized vehicle-frame magnetic direction. It stays untrusted until the phone is fixed in the vehicle and the course calibration completes. |
| Build and focused regression tests | `flutter analyze`: no issues; `flutter test`: 6 passed; the updated debug APK built and installed successfully. Physical fixed-mount calibration remains pending. |

This is a static-device smoke test, not a replacement for the held-out
IO-VNBD replay. A moving 50 m/1 km blackout run is still required to measure
physical-device drift, transition latency, and update rate.

## Reproduce locally

```powershell
$env:PYTHONPATH = 'ml/.vendor;ml/src;edge/python/src'
py -3.13 -m unittest discover -s ml/tests -v
py -3.13 -m unittest discover -s edge/python/tests -v
py -3.13 ml/scripts/stage12_evaluate.py

& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' analyze
& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' test
& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' build apk --debug
```

The held-out replay is an offline dataset result. The physical Android smoke
test above validates sensor, GNSS, and static motion safety; it does not yet
validate moving-device drift or the <10% target.
