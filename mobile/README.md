# MERIDIAN Flutter app

MERIDIAN is the Android client for the Intelligent Dead Reckoning pipeline.
It uses `sensors_plus` and `geolocator` for live device input, runs the shared
TinyVelocityCNN TFLite model on 2-second IMU windows, and exposes the resulting
telemetry in Developer Mode. Training, shared preprocessing definitions, and the
portable edge runtime remain outside this directory.

The app contains the following live states:

- The branded sensor/mapping-engine splash screen.
- A map with a live location marker, destination selection, route preview, and
  speed/accuracy/heading statistics.
- GNSS-aided, GNSS-lost dead-reckoning, and short reacquisition-blend states.
- Developer Mode with the real device sensor axes, physical GNSS, last accurate
  position, prediction, and calculated position-error benchmark.

## Run

Flutter is available in the companion MOVE workspace on this machine. From this
directory, use either a Flutter installation on `PATH` or:

```powershell
& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' pub get
& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' run
```

The first run requires location permission. The map uses OpenStreetMap tiles, so
it requires a network connection; navigation prediction continues when GNSS is
disabled from Developer Mode. That toggle keeps raw physical GNSS available only
for the comparison card and never feeds it into the displayed prediction.

## Verify

```powershell
& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' analyze
& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' test
& 'C:\Projects\Move\.flutter_sdk\flutter\bin\flutter.bat' build apk --debug
```

The debug APK is written to `build/app/outputs/flutter-apk/app-debug.apk`.
