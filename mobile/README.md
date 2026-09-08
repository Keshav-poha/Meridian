# MERIDIAN Flutter app

MERIDIAN is the Android client for the Intelligent Dead Reckoning pipeline.
It uses `sensors_plus` and `geolocator` for live device input, keeps Android's
best-for-navigation location stream as the primary GNSS source, and runs the
shared TinyVelocityCNN TFLite model on 2-second IMU windows. The navigation
speed is GNSS-anchored and gravity-compensated; a bounded CNN can contribute
only a small change-of-velocity residual during a valid blackout. Training,
shared preprocessing definitions, and the portable edge runtime remain outside
this directory.

The app contains the following live states:

- The branded sensor/mapping-engine splash screen.
- A map with a live location marker, destination selection, route preview, and
  speed/accuracy/heading statistics.
- GNSS-aided, GNSS-lost dead-reckoning, and short reacquisition-blend states.
- Developer Mode with the real device sensor axes, physical GNSS, last accurate
  position, prediction, and calculated position-error benchmark.

## Run

From this directory, with Flutter installed and available on `PATH`:

```powershell
flutter pub get
flutter run
```

The first run requires location permission. Current, valid Android fixes up to
100 m accuracy keep navigation available; only fixes at 25 m or better are
used for sensitive aiding/calibration. A repeated stationary timestamp is
treated as a healthy stream heartbeat when the position remains plausible.
The map persistently caches tiles that have already been viewed, allowing those
areas to remain visible when the network is unavailable. While valid GNSS is
available, the app also caches nearby road geometry for use during a subsequent
outage. Road constraints run only on dead-reckoning predictions and fail open
when the road candidate is distant, ambiguous, or heading-inconsistent. Raw
GNSS is never snapped, so valid walking, parking, and off-road positions remain
unchanged. Navigation prediction continues when GNSS is disabled from Developer
Mode. That toggle keeps raw physical GNSS available only for the comparison card
and never feeds it into the displayed prediction.

## Verify

```powershell
flutter analyze
flutter test
flutter build apk --debug
```

The debug APK is written to `build/app/outputs/flutter-apk/app-debug.apk`.
