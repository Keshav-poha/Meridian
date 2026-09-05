import 'dart:async';
import 'dart:math' as math;

import 'package:geolocator/geolocator.dart';
import 'package:sensors_plus/sensors_plus.dart';

import '../domain/telemetry_snapshot.dart';
import 'idr_engine.dart';
import 'motion_gate.dart';
import 'tflite_velocity_estimator.dart';
import 'trip_recorder.dart';

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
  static const _reacquisitionBlend = Duration(milliseconds: 500);
  static const _maximumAidingAccuracyM = 25.0;
  static const _maximumDrSpeedMps = 45.0;
  static const _maximumDrSpeedStepMps = 7.0;

  final StreamController<TelemetrySnapshot> _telemetry =
      StreamController<TelemetrySnapshot>.broadcast();
  final MotionGate _motionGate = MotionGate();
  final VehicleMotionLatch _vehicleMotionLatch = VehicleMotionLatch();
  final GnssRecoveryGate _gnssRecoveryGate = GnssRecoveryGate();
  final TripRecorder _tripRecorder = TripRecorder();

  StreamSubscription<Position>? _positionSubscription;
  StreamSubscription<AccelerometerEvent>? _accelerometerSubscription;
  StreamSubscription<GyroscopeEvent>? _gyroscopeSubscription;
  StreamSubscription<MagnetometerEvent>? _magnetometerSubscription;
  Timer? _ticker;
  TfliteVelocityEstimator? _velocityEstimator;
  Position? _actualPosition;
  Position? _lastGoodGnss;
  DateTime? _lastAcceptedGnssTimestamp;
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
  bool _mountDegraded = false;
  bool _motionAnomaly = false;
  bool _gnssDegraded = false;
  double _predictionConfidence = 0;
  String _predictionConfidenceReason = 'Awaiting calibrated prediction';
  double? _lastAcceptedDrSpeedMps;
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
      final now = DateTime.now();
      _actualPosition = position;
      // Keep physical fixes only as Developer Mode ground truth during a
      // simulated outage; they must not recalibrate the inertial model.
      if (!_gnssEnabled) return;
      if (!_isUsableGnssFix(position, now) ||
          !_isPlausibleAidingFix(position)) {
        _gnssDegraded = true;
        return;
      }
      // Do not reacquire from a callback carrying a cached pre-loss fix.  The
      // raw position remains visible in Developer Mode, but aiding waits for a
      // fix timestamped after the loss/enable transition.
      if (!_gnssRecoveryGate.accept(position.timestamp)) {
        _gnssDegraded = true;
        return;
      }
      _gnssDegraded = position.accuracy > 10.0;
      _lastGoodGnss = position;
      _lastAcceptedGnssTimestamp = position.timestamp;
      final courseReliable = _hasUsableCourse(position);
      if (courseReliable) {
        _velocityEstimator?.setGnssReference(
          speedMps: position.speed,
          headingDeg: position.heading,
          accuracyM: position.accuracy,
          timestamp: position.timestamp,
          headingReliable: true,
        );
        _headingDegrees = position.heading;
      }
      if (_hasUsableSpeed(position)) {
        _lastAcceptedDrSpeedMps = position.speed;
      }
      final fix = _LocalPosition(position.latitude, position.longitude);
      _lastAccurate = fix;
      _predicted ??= fix;
    });
  }

  @override
  Future<void> setGnssEnabled(bool enabled) async {
    if (_gnssEnabled == enabled) return;
    _gnssEnabled = enabled;
    _tripRecorder.recordEvent('gnss_aiding_changed', fields: <String, Object?>{
      'enabled': enabled,
      'note': 'Manual outage simulator; physical GNSS remains reference-only.',
    });
    if (!enabled) {
      _gnssRecoveryGate.requireFixAfter(DateTime.now());
      _lossStarted = DateTime.now();
      _reacquireStarted = null;
      _predicted ??= _lastAccurate;
      if (_started) _tick();
      return;
    }
    // A physical provider may have queued a fix while aiding was disabled.
    // Require one collected after this re-enable event before blending back.
    _gnssRecoveryGate.requireFixAfter(DateTime.now());
    if (_started) _tick();
  }

  @override
  Future<void> startTripRecording({required String label}) =>
      _tripRecorder.start(label: label);

  @override
  Future<String?> stopTripRecording() => _tripRecorder.stop();

  void _tick() {
    final now = DateTime.now();
    final previousTick = _lastTick ?? now;
    final rawDt =
        math.max(0.0, now.difference(previousTick).inMicroseconds / 1e6);
    // A foreground/background pause must not produce a large invented step.
    final dt = rawDt.clamp(0.001, 0.25).toDouble();
    _lastTick = now;
    final freshFix = _isFreshGoodFix(now);
    final gnssReportsMotion = _gnssEnabled &&
        freshFix &&
        _lastGoodGnss != null &&
        _hasUsableSpeed(_lastGoodGnss!);
    final inferred = _velocityEstimator?.estimate();
    if (inferred != null) {
      // This diagnostic is intentionally separate from the navigation value:
      // no clipping turns an unsafe model output into a trusted measurement.
      _modelSpeedMps = inferred.speedMps;
      _velocityModelTrusted = inferred.isTrusted;
      _mountCalibrated = inferred.mountCalibrated;
      _mountDegraded = inferred.mountDegraded;
      _motionAnomaly = inferred.motionAnomaly;
    }
    _vehicleMotionArmed = _vehicleMotionLatch.update(
      gnssEnabled: _gnssEnabled,
      gnssFixFresh: freshFix,
      gnssReportsMotion: gnssReportsMotion,
    );
    _stationary = _motionGate.update(
      accelerometer: _accelerometer,
      gyroscope: _gyroscope,
      gnssReportsMotion: gnssReportsMotion,
      externalMotionAnomaly: _motionAnomaly,
    );
    final mode = _navigationMode(freshFix, now);
    final trustedDrVelocity = mode == NavigationMode.deadReckoning &&
        _vehicleMotionLatch.armed &&
        _velocityModelTrusted &&
        !_stationary;
    // In GNSS-aided mode, GNSS speed is the measured reference. The CNN is
    // reserved for a valid, calibrated blackout window rather than overriding
    // a good satellite measurement with a model extrapolation.
    _speedMps = gnssReportsMotion
        ? _lastGoodGnss!.speed
        : trustedDrVelocity
            ? _safeDrSpeed(_modelSpeedMps, dt)
            : 0.0;

    final sourceHeading =
        freshFix && _lastGoodGnss != null && _hasUsableCourse(_lastGoodGnss!)
            ? _lastGoodGnss!.heading
            : null;
    // Never integrate raw phone-frame Z here. The estimator exposes only the
    // vehicle-frame yaw rate after its mount transform is trustworthy.
    if (sourceHeading != null) {
      _headingDegrees = sourceHeading;
    } else if (inferred != null &&
        inferred.mountCalibrated &&
        inferred.sensorsFresh &&
        !inferred.mountDegraded) {
      _headingDegrees += inferred.vehicleYawRateRps * dt * 180 / math.pi;
    }
    _headingDegrees %= 360;
    if (_headingDegrees < 0) _headingDegrees += 360;
    // GNSS-aided display is corrected directly by incoming fixes; only
    // advance the inertial position during an outage or reacquisition.
    if (mode != NavigationMode.gnssAidedIns) {
      _advancePrediction(
        dt,
        countDrDistance: mode == NavigationMode.deadReckoning,
      );
    }
    _setPredictionConfidence(inferred, freshFix, mode, now);
    final display = _displayPosition(mode, now);
    if (display == null) return;
    final actual = _actualPosition == null
        ? null
        : _LocalPosition(_actualPosition!.latitude, _actualPosition!.longitude);
    final error =
        actual == null ? double.nan : _distanceMeters(display, actual);
    final snapshot = TelemetrySnapshot(
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
      predictionConfidence: _predictionConfidence,
      predictionConfidenceReason: _predictionConfidenceReason,
      mountDegraded: _mountDegraded,
      motionAnomaly: _motionAnomaly || _motionGate.inShockCooldown,
      gnssDegraded: _gnssDegraded,
    );
    _tripRecorder.record(snapshot);
    _telemetry.add(snapshot);
  }

  NavigationMode _navigationMode(bool freshFix, DateTime now) {
    if (!_gnssEnabled || !freshFix) {
      // Capture this boundary once. Replacing it every 100 ms would reject a
      // legitimate provider fix whose timestamp trails its delivery time.
      if (_gnssEnabled && _lossStarted == null) {
        _gnssRecoveryGate.requireFixAfter(now);
      }
      _lossStarted ??= now;
      _reacquireStarted = null;
      return NavigationMode.deadReckoning;
    }
    if (_lossStarted != null && _reacquireStarted == null) {
      _reacquireFrom = _predicted ?? _lastAccurate;
      _reacquireStarted = now;
    }
    if (_reacquireStarted != null &&
        now.difference(_reacquireStarted!) < _reacquisitionBlend) {
      return NavigationMode.reacquiring;
    }
    _reacquireStarted = null;
    _lossStarted = null;
    _drDistanceM = 0;
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
      final progress = (now.difference(_reacquireStarted!).inMilliseconds /
              _reacquisitionBlend.inMilliseconds)
          .clamp(0.0, 1.0)
          .toDouble();
      return _LocalPosition.lerp(_reacquireFrom!, _lastAccurate!, progress);
    }
    return _predicted ?? _lastAccurate;
  }

  void _advancePrediction(double dt, {required bool countDrDistance}) {
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
    if (countDrDistance) _drDistanceM += distance;
  }

  bool _isFreshGoodFix(DateTime now) {
    if (_gnssRecoveryGate.awaitingFreshFix) return false;
    final fix = _lastGoodGnss;
    if (fix == null) return false;
    final age = now.difference(fix.timestamp);
    return age >= Duration.zero && age <= _gnssFixTimeout;
  }

  bool _isUsableGnssFix(Position position, DateTime now) =>
      !position.isMocked &&
      position.hasAccuracy &&
      position.accuracy.isFinite &&
      position.accuracy > 0 &&
      position.accuracy <= _maximumAidingAccuracyM &&
      position.latitude.isFinite &&
      position.longitude.isFinite &&
      position.latitude.abs() <= 90 &&
      position.longitude.abs() <= 180 &&
      !position.timestamp.isAfter(now.add(const Duration(seconds: 3)));

  bool _isPlausibleAidingFix(Position candidate) {
    final previous = _lastGoodGnss;
    final previousTimestamp = _lastAcceptedGnssTimestamp;
    if (previous == null || previousTimestamp == null) return true;
    if (!candidate.timestamp.isAfter(previousTimestamp)) return false;
    final elapsedS =
        candidate.timestamp.difference(previousTimestamp).inMilliseconds / 1000;
    if (elapsedS <= 0) return false;
    final previousPoint = _LocalPosition(previous.latitude, previous.longitude);
    final candidatePoint =
        _LocalPosition(candidate.latitude, candidate.longitude);
    final distance = _distanceMeters(previousPoint, candidatePoint);
    // A bounded road-speed envelope plus measured accuracy rejects recent
    // multipath teleports. Coarse but usable fixes stay visibly degraded.
    final allowedDistance = math.max(
      40.0,
      elapsedS * 55.0 + 1.5 * math.max(previous.accuracy, candidate.accuracy),
    );
    return distance <= allowedDistance;
  }

  bool _hasUsableSpeed(Position position) =>
      position.hasSpeed &&
      position.speed.isFinite &&
      position.speed >= 0.7 &&
      position.speed <= _maximumDrSpeedMps &&
      (!position.hasSpeedAccuracy ||
          (position.speedAccuracy.isFinite && position.speedAccuracy <= 3));

  bool _hasUsableCourse(Position position) =>
      _hasUsableSpeed(position) &&
      position.hasHeading &&
      position.heading.isFinite &&
      position.heading >= 0 &&
      position.heading < 360 &&
      position.speed >= 2.0 &&
      (!position.hasHeadingAccuracy ||
          (position.headingAccuracy.isFinite &&
              position.headingAccuracy <= 35));

  double _safeDrSpeed(double candidate, double dt) {
    if (!candidate.isFinite ||
        candidate < 0 ||
        candidate > _maximumDrSpeedMps) {
      return 0;
    }
    final previous = _lastAcceptedDrSpeedMps;
    final allowedStep = math.max(_maximumDrSpeedStepMps, 8.0 * dt);
    // Reject, rather than clip, an abrupt network spike. A new accepted
    // estimate establishes the next plausible comparison point.
    if (previous != null && (candidate - previous).abs() > allowedStep) {
      return 0;
    }
    _lastAcceptedDrSpeedMps = candidate;
    return candidate;
  }

  void _setPredictionConfidence(
    VelocityEstimate? inferred,
    bool freshFix,
    NavigationMode mode,
    DateTime now,
  ) {
    if (mode == NavigationMode.gnssAidedIns &&
        freshFix &&
        _lastGoodGnss != null) {
      _predictionConfidence =
          (1 - _lastGoodGnss!.accuracy / _maximumAidingAccuracyM)
              .clamp(0.2, 1.0)
              .toDouble();
      _predictionConfidenceReason = _gnssDegraded
          ? 'GNSS aided with degraded accuracy (${_lastGoodGnss!.accuracy.toStringAsFixed(1)} m)'
          : 'GNSS aided (${_lastGoodGnss!.accuracy.toStringAsFixed(1)} m accuracy)';
      return;
    }
    if (inferred == null) {
      _predictionConfidence = 0;
      _predictionConfidenceReason = 'Awaiting 2 s calibrated IMU window';
      return;
    }
    final reasons = <String>[];
    var confidence = inferred.confidence;
    if (!_vehicleMotionArmed) {
      confidence = 0;
      reasons.add('awaiting GNSS-confirmed vehicle motion');
    }
    if (_stationary) {
      confidence = 0;
      reasons.add('stationary gate');
    }
    if (_motionGate.inShockCooldown || inferred.motionAnomaly) {
      confidence = 0;
      reasons.add('shock / mount-motion cooldown');
    }
    if (inferred.mountDegraded) reasons.add('mount calibration degraded');
    if (!inferred.mountCalibrated) reasons.add('mount calibration pending');
    if (!inferred.sensorsFresh) reasons.add('stale IMU channel');
    if (!inferred.quality.isInDistribution) {
      reasons.add('model input out of distribution');
    }
    if (_lossStarted != null) {
      final blackoutS = now.difference(_lossStarted!).inMilliseconds / 1000;
      confidence *= math.exp(-blackoutS / 120);
    }
    _predictionConfidence = confidence.clamp(0.0, 1.0).toDouble();
    _predictionConfidenceReason = reasons.isEmpty
        ? 'Calibrated vehicle-frame IMU prediction'
        : reasons.join(' • ');
  }

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
    await _tripRecorder.stop();
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
