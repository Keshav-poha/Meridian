# Test results

This page records the most recent local verification of the committed
repository. The GitHub Actions workflow in `.github/workflows/verify.yml`
repeats the unit and build checks on every push and pull request.

## 4 September 2026

| Target | Command | Result |
| --- | --- | --- |
| Shared ML pipeline | `py -3.13 -m unittest discover -s ml/tests -v` | 5 passed in 0.064 s |
| Edge preprocessing | `py -3.13 -m unittest discover -s edge/python/tests -v` | 1 passed in 0.027 s |
| Flutter static analysis | `flutter analyze` | No issues (10.6 s) |
| Flutter controller test | `flutter test` | 1 passed |
| Android package | `flutter build apk --debug` | Built successfully; 173,619,646-byte APK |
| IO-VNBD held-out replay | `py -3.13 ml/scripts/stage12_evaluate.py` | 6.99% endpoint drift (47.35 m / 676.92 m) at 10 Hz; passes the <10% requirement |

The position plot, sample-level trajectory, and machine-readable metric report
are in [Stage 12 evaluation](evaluation/stage12/README.md).

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

The held-out replay is an offline dataset result. No physical Android device
has been used for a sensor/GNSS permission, latency, or live-navigation test
yet; that is the next validation step.
