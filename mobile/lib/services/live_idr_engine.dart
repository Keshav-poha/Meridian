import 'dart:async';
import 'dart:math' as math;

import 'package:geolocator/geolocator.dart';
import 'package:sensors_plus/sensors_plus.dart';

import '../domain/telemetry_snapshot.dart';
import 'idr_engine.dart';
import 'motion_gate.dart';
import 'tflite_velocity_estimator.dart';

/// Live mobile bridge for the Stage 4/5/9 pipeline.
///
/// GNSS can be disabled without stopping its physical stream. In that case its
/// positions are retained strictly as a Developer Mode ground-truth comparator;
/// they are never used to update the displayed inertial position.
class LiveIdrEngine implements IdrEngine {
  // Android may batch stationary fused-location callbacks even when a
  // one-second interval is requested. Keep the UI in GNSS-aided mode across
  // that normal five-second cadence; manual outage simulation remains
  // immediate because it bypasses this watchdog.
  static const _gnssFixTimeout = Duration(seconds: 8);

  final StreamController<TelemetrySnapshot> _telemetry =
      StreamController<TelemetrySnapshot>.broadcast();
  final MotionGate _motionGate = MotionGate();
  final VehicleMotionLatch _vehicleMotionLatch = VehicleMotionLatch();

  StreamSubscription<Position>? _positionSubscription;
  StreamSubscription<AccelerometerEvent>? _accelerometerSubscription;
  StreamSubscription<GyroscopeEvent>? _gyroscopeSubscription;
  StreamSubscription<MagnetometerEvent>? _magnetometerSubscription;
  Timer? _ticker;
  TfliteVelocityEstimator? _velocityEstimator;
  Position? _actualPosition;
  DateTime? _lastTick;
  DateTime? _lossStarted;
  DateTime? _reacquireStarted;
  _LocalPosition? _lastAccurate;
  _LocalPosition? _predicted;
  _LocalPosition? _reacquireFrom;
  Axis3 _accelerometer = const Axis3(0, 0, 0);
  Axis3 _gyroscope = const Axis3(0, 0, 0);
  Axis3 _magnetometer = const Axis3(0, 0, 0);
  double _headingDegrees = 0;
  double _speedMps = 0;
  double _modelSpeedMps = 0;
  bool _velocityModelTrusted = false;
  bool _mountCalibrated = false;
  double _drDistanceM = 0;
  bool _stationary = false;
  bool _vehicleMotionArmed = false;
  bool _started = false;
  bool _gnssEnabled = true;

  @override
  Stream<TelemetrySnapshot> get telemetry => _telemetry.stream;

  @override
  Future<void> start() async {
    if (_started) return;
    _started = true;
    _velocityEstimator = await TfliteVelocityEstimator.create();
    _velocityEstimator!.start();
    _accelerometerSubscription = accelerometerEventStream().listen((event) {
      _accelerometer = Axis3(event.x, event.y, event.z);
    });
    _gyroscopeSubscription = gyroscopeEventStream().listen((event) {
      _gyroscope = Axis3(event.x, event.y, event.z);
    });
    _magnetometerSubscription = magnetometerEventStream().listen((event) {
      _magnetometer = Axis3(event.x, event.y, event.z);
    });
    await _startPositionStream();
    _lastTick = DateTime.now();
    _ticker = Timer.periodic(const Duration(milliseconds: 100), (_) => _tick());
  }

  Future<void> _startPositionStream() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      return;
    }
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      return;
    }
    final settings = AndroidSettings(
      accuracy: LocationAccuracy.bestForNavigation,
      distanceFilter: 0,
      intervalDuration: Duration(seconds: 1),
    );
    _positionSubscription =
        Geolocator.getPositionStream(locationSettings: settings)
            .listen((position) {
      _actualPosition = position;
      // Keep physical fixes only as Developer Mode ground truth during a
      // simulated outage; they must not recalibrate the inertial model.
      if (!_gnssEnabled) return;
      _velocityEstimator?.setGnssReference(
        speedMps: position.speed,
        headingDeg: position.heading,
      );
      _headingDegrees = _validHeading(position.heading) ?? _headingDegrees;
      final fix = _LocalPosition(position.latitude, position.longitude);
      _lastAccurate = fix;
      _predicted ??= fix;
      _drDistanceM = 0;
      _lossStarted = null;
    });
  }

  @override
  Future<void> setGnssEnabled(bool enabled) async {
    if (_gnssEnabled == enabled) return;
    _gnssEnabled = enabled;
    if (!enabled) {
      _lossStarted = DateTime.now();
      _reacquireStarted = null;
      _predicted ??= _lastAccurate;
      if (_started) _tick();
      return;
    }
    _reacquireStarted = DateTime.now();
    _reacquireFrom = _predicted;
    if (_started) _tick();
  }

  void _tick() {
    final now = DateTime.now();
    final previousTick = _lastTick ?? now;
    final dt =
        math.max(0.001, now.difference(previousTick).inMicroseconds / 1e6);
    _lastTick = now;
    final freshFix = _actualPosition != null &&
        now.difference(_actualPosition!.timestamp) <= _gnssFixTimeout;
    final gnssReportsMotion = _gnssEnabled &&
        freshFix &&
        _actualPosition!.speed.isFinite &&
        _actualPosition!.speed >= 0.7;
    _vehicleMotionArmed = _vehicleMotionLatch.update(
      gnssEnabled: _gnssEnabled,
      gnssFixFresh: freshFix,
      gnssReportsMotion: gnssReportsMotion,
    );
    _stationary = _motionGate.update(
      accelerometer: _accelerometer,
      gyroscope: _gyroscope,
      gnssReportsMotion: gnssReportsMotion,
    );
    final inferred = _velocityEstimator?.estimate();
    if (inferred != null) {
      // Retain a bounded diagnostic value for Developer Mode, but never let an
      // out-of-distribution model response participate in navigation.
      _modelSpeedMps = inferred.speedMps.clamp(0.0, 55.0).toDouble();
      _velocityModelTrusted = inferred.isTrusted;
      _mountCalibrated = inferred.mountCalibrated;
    }
    final trustedDrVelocity = !_gnssEnabled &&
        _vehicleMotionLatch.armed &&
        _velocityModelTrusted &&
        !_stationary;
    // In GNSS-aided mode, GNSS speed is the measured reference. The CNN is
    // reserved for a valid, calibrated blackout window rather than overriding
    // a good satellite measurement with a model extrapolation.
    _speedMps = gnssReportsMotion
        ? _actualPosition!.speed
        : trustedDrVelocity
            ? _modelSpeedMps
            : 0.0;

    final sourceHeading = _actualPosition == null
        ? null
        : _validHeading(_actualPosition!.heading);
    _headingDegrees = sourceHeading ??
        ((_headingDegrees + _gyroscope.z * dt * 180 / math.pi) % 360);
    if (_headingDegrees < 0) _headingDegrees += 360;
    final mode = _navigationMode(freshFix);
    // GNSS-aided display is corrected directly by incoming fixes; only
    // advance the inertial position during an outage or reacquisition.
    if (mode != NavigationMode.gnssAidedIns) _advancePrediction(dt);
    final display = _displayPosition(mode, now);
    if (display == null) return;
    final actual = _actualPosition == null
        ? null
        : _LocalPosition(_actualPosition!.latitude, _actualPosition!.longitude);
    final error =
        actual == null ? double.nan : _distanceMeters(display, actual);
    _telemetry.add(TelemetrySnapshot(
      timestamp: now,
      mode: mode,
      speedMps: _speedMps,
      modelSpeedMps: _modelSpeedMps,
      velocityModelTrusted: _velocityModelTrusted,
      mountCalibrated: _mountCalibrated,
      stationary: _stationary,
      vehicleMotionArmed: _vehicleMotionArmed,
      headingDeg: _headingDegrees,
      latitudeDeg: display.latitude,
      longitudeDeg: display.longitude,
      accuracyM: _actualPosition?.accuracy ?? double.nan,
      positionErrorM: error,
      driftPercent: _drDistanceM <= 0 || !error.isFinite
          ? double.nan
          : error / _drDistanceM * 100,
      accelerometer: _accelerometer,
      gyroscope: _gyroscope,
      magnetometer: _magnetometer,
      gnssAvailable: _gnssEnabled && freshFix,
      gnssEnabled: _gnssEnabled,
      lastAccurateLatitudeDeg: _lastAccurate?.latitude,
      lastAccurateLongitudeDeg: _lastAccurate?.longitude,
      actualLatitudeDeg: actual?.latitude,
      actualLongitudeDeg: actual?.longitude,
      deadReckoningDistanceM: _drDistanceM,
      deadReckoningElapsed:
          _lossStarted == null ? Duration.zero : now.difference(_lossStarted!),
    ));
  }

  NavigationMode _navigationMode(bool freshFix) {
    if (!_gnssEnabled || !freshFix) return NavigationMode.deadReckoning;
    if (_reacquireStarted != null &&
        DateTime.now().difference(_reacquireStarted!) <
            const Duration(milliseconds: 500)) {
      return NavigationMode.reacquiring;
    }
    _reacquireStarted = null;
    return NavigationMode.gnssAidedIns;
  }

  _LocalPosition? _displayPosition(NavigationMode mode, DateTime now) {
    if (mode == NavigationMode.gnssAidedIns) {
      if (_lastAccurate != null) _predicted = _lastAccurate;
      return _lastAccurate;
    }
    if (mode == NavigationMode.reacquiring &&
        _lastAccurate != null &&
        _reacquireFrom != null) {
      final progress = (now.difference(_reacquireStarted!).inMilliseconds / 500)
          .clamp(0.0, 1.0)
          .toDouble();
      return _LocalPosition.lerp(_reacquireFrom!, _lastAccurate!, progress);
    }
    return _predicted ?? _lastAccurate;
  }

  void _advancePrediction(double dt) {
    final position = _predicted ?? _lastAccurate;
    if (position == null) return;
    final heading = _headingDegrees * math.pi / 180;
    final distance = _speedMps * dt;
    final north = distance * math.cos(heading);
    final east = distance * math.sin(heading);
    _predicted = _LocalPosition(
      position.latitude + north / 111320,
      position.longitude +
          east / (111320 * math.cos(position.latitude * math.pi / 180)),
    );
    if (!_gnssEnabled) _drDistanceM += distance;
  }

  double? _validHeading(double heading) =>
      heading.isFinite && heading >= 0 ? heading : null;

  double _distanceMeters(_LocalPosition left, _LocalPosition right) {
    const earthRadiusM = 6371000.0;
    final lat1 = left.latitude * math.pi / 180;
    final lat2 = right.latitude * math.pi / 180;
    final dLat = lat2 - lat1;
    final dLon = (right.longitude - left.longitude) * math.pi / 180;
    final a = math.sin(dLat / 2) * math.sin(dLat / 2) +
        math.cos(lat1) *
            math.cos(lat2) *
            math.sin(dLon / 2) *
            math.sin(dLon / 2);
    return 2 * earthRadiusM * math.atan2(math.sqrt(a), math.sqrt(1 - a));
  }

  @override
  Future<void> stop() async {
    _ticker?.cancel();
    await _positionSubscription?.cancel();
    await _accelerometerSubscription?.cancel();
    await _gyroscopeSubscription?.cancel();
    await _magnetometerSubscription?.cancel();
    await _velocityEstimator?.dispose();
    _started = false;
  }
}

class _LocalPosition {
  const _LocalPosition(this.latitude, this.longitude);
  final double latitude;
  final double longitude;

  factory _LocalPosition.lerp(
          _LocalPosition from, _LocalPosition to, double amount) =>
      _LocalPosition(
        from.latitude + (to.latitude - from.latitude) * amount,
        from.longitude + (to.longitude - from.longitude) * amount,
      );
}
