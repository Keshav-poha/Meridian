import 'dart:async';
import 'dart:math' as math;

import 'package:geolocator/geolocator.dart';
import 'package:sensors_plus/sensors_plus.dart';

import '../domain/telemetry_snapshot.dart';
import 'idr_engine.dart';
import 'ins_speed_filter.dart';
import 'motion_gate.dart';
import 'native_gnss_stream.dart';
import 'tflite_velocity_estimator.dart';
import 'trip_recorder.dart';

/// Live mobile bridge for the Stage 4/5/9 pipeline.
///
/// GNSS can be disabled without stopping its physical stream. In that case its
/// positions are retained strictly as a Developer Mode ground-truth comparator;
/// they are never used to update the displayed inertial position.
class LiveIdrEngine implements IdrEngine {
  // A stream heartbeat, rather than a single provider's timestamp, defines
  // availability. Android may legitimately repeat a stationary location's
  // timestamp while the high-accuracy provider is still healthy. Four seconds
  // avoids false GPS-loss flashes while still declaring a genuine service loss
  // without delay.
  static const _gnssFixTimeout = Duration(seconds: 4);
  static const _reacquisitionBlend = Duration(milliseconds: 500);
  static const _maximumNavigationAccuracyM = 100.0;
  static const _maximumAidingAccuracyM = 25.0;
  static const _maximumDrSpeedMps = 45.0;
  static const _modelInitializationTimeout = Duration(seconds: 8);
  static const _maximumBootstrapFixAge = Duration(minutes: 2);
  static const _maximumNavigationFixAge = Duration(seconds: 15);

  final StreamController<TelemetrySnapshot> _telemetry =
      StreamController<TelemetrySnapshot>.broadcast();
  final MotionGate _motionGate = MotionGate();
  final VehicleMotionLatch _vehicleMotionLatch = VehicleMotionLatch();
  final GnssRecoveryGate _gnssRecoveryGate = GnssRecoveryGate();
  final InsSpeedFilter _insSpeedFilter = InsSpeedFilter();
  final TripRecorder _tripRecorder = TripRecorder();
  final NativeGnssStream _nativeGnssStream = NativeGnssStream();

  // Keep Flutter's best-for-navigation stream as the primary source. This is
  // the stream users previously observed to be reliable. The native GPS
  // provider is supplemental: losing it must not mark a healthy high-accuracy
  // Android location as lost.
  StreamSubscription<Position>? _positionSubscription;
  StreamSubscription<Position>? _nativePositionSubscription;
  StreamSubscription<AccelerometerEvent>? _accelerometerSubscription;
  StreamSubscription<GyroscopeEvent>? _gyroscopeSubscription;
  StreamSubscription<MagnetometerEvent>? _magnetometerSubscription;
  Timer? _ticker;
  TfliteVelocityEstimator? _velocityEstimator;
  Position? _actualPosition;
  Position? _lastGoodGnss;
  DateTime? _lastAcceptedGnssTimestamp;
  DateTime? _lastAcceptedGnssReceiptAt;
  DateTime? _lastTick;
  DateTime? _lossStarted;
  DateTime? _reacquireStarted;
  _LocalPosition? _lastAccurate;
  _LocalPosition? _predicted;
  _LocalPosition? _reacquireFrom;
  _LocalPosition? _bootstrapPosition;
  DateTime? _bootstrapPositionTimestamp;
  DateTime? _firstInitialQualityFixTimestamp;
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
  String? _velocityModelStartupWarning;
  double _drDistanceM = 0;
  bool _stationary = false;
  bool _vehicleMotionArmed = false;
  bool _started = false;
  bool _gnssEnabled = true;
  int _lifecycleGeneration = 0;
  String _locationStatus = 'Checking device location service';

  @override
  Stream<TelemetrySnapshot> get telemetry => _telemetry.stream;

  @override
  Future<void> start() async {
    if (_started) return;
    _started = true;
    // A restarted navigation session must not inherit a speed anchor from a
    // previous trip before it receives a new measured GNSS speed.
    _insSpeedFilter.reset();
    final lifecycleGeneration = ++_lifecycleGeneration;
    try {
      // Raw sensors and the 10 Hz ticker are the minimal live runtime.  Start
      // them first; TFLite and location setup are allowed to finish later.
      _accelerometerSubscription = accelerometerEventStream().listen(
        (event) => _accelerometer = Axis3(event.x, event.y, event.z),
        onError: (_, __) => _recordSensorWarning('Accelerometer unavailable'),
      );
      _gyroscopeSubscription = gyroscopeEventStream().listen(
        (event) => _gyroscope = Axis3(event.x, event.y, event.z),
        onError: (_, __) => _recordSensorWarning('Gyroscope unavailable'),
      );
      _magnetometerSubscription = magnetometerEventStream().listen(
        (event) => _magnetometer = Axis3(event.x, event.y, event.z),
        onError: (_, __) => _recordSensorWarning('Magnetometer unavailable'),
      );
      _lastTick = DateTime.now();
      _ticker =
          Timer.periodic(const Duration(milliseconds: 100), (_) => _tick());

      // A permission dialog or a slow native interpreter must not hold the
      // Flutter shell on its splash screen.  Both paths fail closed: no model
      // speed is trusted until initialization genuinely completes.
      unawaited(_initializeVelocityEstimator(lifecycleGeneration));
      unawaited(_initializePositionStream(lifecycleGeneration));
    } catch (_) {
      _started = false;
      await stop();
      rethrow;
    }
  }

  bool _isActive(int lifecycleGeneration) =>
      _started && _lifecycleGeneration == lifecycleGeneration;

  Future<void> _initializeVelocityEstimator(int lifecycleGeneration) async {
    final creation = TfliteVelocityEstimator.create();
    try {
      final estimator = await creation.timeout(_modelInitializationTimeout);
      if (!_isActive(lifecycleGeneration)) {
        await estimator.dispose();
        return;
      }
      estimator.start();
      _velocityEstimator = estimator;
      _velocityModelStartupWarning = null;
    } on TimeoutException {
      if (_isActive(lifecycleGeneration)) {
        _velocityModelStartupWarning = 'Velocity model startup timed out';
      }
      // Interpreter creation cannot be cancelled. Recover if the app is
      // still on this lifecycle when it eventually finishes; otherwise
      // dispose it so a closed/restarted screen cannot retain native state.
      unawaited(creation.then<void>(
        (estimator) async {
          if (!_isActive(lifecycleGeneration) || _velocityEstimator != null) {
            await estimator.dispose();
            return;
          }
          estimator.start();
          _velocityEstimator = estimator;
          _velocityModelStartupWarning = null;
        },
        onError: (Object _, StackTrace __) {},
      ));
    } catch (_) {
      if (_isActive(lifecycleGeneration)) {
        _velocityModelStartupWarning = 'Velocity model unavailable';
      }
    }
  }

  Future<void> _initializePositionStream(int lifecycleGeneration) async {
    try {
      await _startPositionStream(lifecycleGeneration);
    } catch (_) {
      if (_isActive(lifecycleGeneration)) {
        _setLocationStatus(
            'Location provider setup failed — retry after checking Android location settings');
      }
    }
  }

  void _recordSensorWarning(String warning) {
    _velocityModelStartupWarning ??= warning;
  }

  Future<void> _startPositionStream(int lifecycleGeneration) async {
    if (!_isActive(lifecycleGeneration)) return;
    if (!await Geolocator.isLocationServiceEnabled()) {
      if (_isActive(lifecycleGeneration)) {
        _setLocationStatus(
          'Location service is off — enable it in Android settings',
        );
      }
      return;
    }
    if (!_isActive(lifecycleGeneration)) return;
    var permission = await Geolocator.checkPermission();
    if (!_isActive(lifecycleGeneration)) return;
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (!_isActive(lifecycleGeneration)) return;
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      _setLocationStatus(
        permission == LocationPermission.deniedForever
            ? 'Location permission is blocked — enable it in Android app settings'
            : 'Location permission denied — allow location to acquire GNSS',
      );
      return;
    }
    // This is the source used by the earlier app version. It combines Android
    // high-accuracy providers and continues to deliver an accurate location on
    // devices whose raw GPS provider is slow to issue its first epoch.
    final settings = AndroidSettings(
      accuracy: LocationAccuracy.bestForNavigation,
      distanceFilter: 0,
      intervalDuration: const Duration(seconds: 1),
    );
    final subscription =
        Geolocator.getPositionStream(locationSettings: settings).listen(
      (position) {
        if (_isActive(lifecycleGeneration)) _onPosition(position);
      },
      onError: (_, __) {
        if (_isActive(lifecycleGeneration)) {
          _setLocationStatus(
            'Android high-accuracy location stream error — waiting for a new fix',
          );
        }
      },
    );
    if (!_isActive(lifecycleGeneration)) {
      await subscription.cancel();
      return;
    }
    _positionSubscription = subscription;

    // Direct GPS observations are useful provenance when present, but no
    // longer form a single point of failure for the user-facing location.
    // A number of Android builds delay GPS_PROVIDER callbacks while their
    // fused best-for-navigation location is already accurate.
    _nativePositionSubscription = _nativeGnssStream.positions.listen(
      (position) {
        if (_isActive(lifecycleGeneration)) _onPosition(position);
      },
      onError: (_, __) {
        // The primary high-accuracy stream stays active. Do not overwrite its
        // healthy status or force a false "GPS LOST" banner when native GPS is
        // temporarily unavailable.
      },
    );
    _setLocationStatus(
      'Android high-accuracy location stream active — waiting for a measured fix',
    );
    // Stream delivery can legitimately take a while on a cold GPS start.
    // Use a recent physical cache and an independent one-shot request to make
    // the map useful immediately, but keep both display-only until they pass
    // the stricter fusion validation below.
    unawaited(_seedBootstrapPosition(
      AndroidSettings(
          accuracy: LocationAccuracy.bestForNavigation,
          distanceFilter: 0,
          intervalDuration: const Duration(seconds: 1),
          timeLimit: const Duration(seconds: 12)),
      lifecycleGeneration,
    ));
  }

  Future<void> _seedBootstrapPosition(
    LocationSettings settings,
    int lifecycleGeneration,
  ) async {
    try {
      final cached = await Geolocator.getLastKnownPosition();
      if (_isActive(lifecycleGeneration) && cached != null) {
        _actualPosition = cached;
        _recordBootstrapPosition(cached, DateTime.now());
      }
      final current = await Geolocator.getCurrentPosition(
        locationSettings: settings,
      );
      // A one-shot provider response is useful for the map, but it may be a
      // current-looking fused/cache result. It is deliberately display-only;
      // only the continuously observed stream may establish a DR origin.
      if (_isActive(lifecycleGeneration)) {
        _actualPosition = current;
        _recordBootstrapPosition(current, DateTime.now());
      }
    } catch (_) {
      // A stream callback remains the primary source; a failed one-shot
      // request must not stop it or turn a coarse location into fusion input.
    }
  }

  void _onPosition(Position position) {
    if (!_started) return;
    final now = DateTime.now();
    _actualPosition = position;
    _recordBootstrapPosition(position, now);
    // Keep physical fixes only as Developer Mode ground truth during a
    // simulated outage; they must not recalibrate the inertial model.
    if (!_gnssEnabled) {
      _tick();
      return;
    }
    if (!_isNavigationGnssFix(position, now) ||
        !_isPlausibleAidingFix(position)) {
      _firstInitialQualityFixTimestamp = null;
      _setLocationStatus(_rejectedLocationStatus(position, now));
      _tick();
      return;
    }
    // Do not reacquire from a callback carrying a cached pre-loss fix.  The
    // raw position remains visible in Developer Mode, but aiding waits for a
    // fix timestamped after the loss/enable transition.
    if (!_gnssRecoveryGate.accept(position.timestamp)) {
      _setLocationStatus(
        'Waiting for a location fix collected after GNSS recovery',
      );
      _tick();
      return;
    }
    if (!_hasInitialQualityFixPair(position, now)) {
      _setLocationStatus(
        'First fusion-quality location received — waiting for a second fresh fix',
      );
      _tick();
      return;
    }
    // Availability and fusion quality are intentionally separate. A visible,
    // current 30–80 m Android fix still means location is available; it must
    // not turn the UI into GPS LOST. It is simply withheld from sensitive
    // calibration/fusion updates until it improves.
    _gnssDegraded = !_isFusionQualityGnssFix(position, now);
    _locationStatus = _gnssDegraded
        ? 'Location accepted with degraded accuracy (${position.accuracy.toStringAsFixed(1)} m)'
        : 'Fusion-quality location accepted';
    _lastGoodGnss = position;
    _lastAcceptedGnssTimestamp = position.timestamp;
    _lastAcceptedGnssReceiptAt = now;
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
    if (_hasMeasuredSpeed(position)) {
      _insSpeedFilter.anchorGnssSpeed(position.speed);
    }
    final fix = _LocalPosition(position.latitude, position.longitude);
    _lastAccurate = fix;
    _predicted ??= fix;
    _tick();
  }

  void _recordBootstrapPosition(Position position, DateTime now) {
    if (!_isDisplayableGnssPosition(position, now)) return;
    _bootstrapPosition = _LocalPosition(position.latitude, position.longitude);
    _bootstrapPositionTimestamp = position.timestamp;
    final accuracy = position.hasAccuracy && position.accuracy.isFinite
        ? '${position.accuracy.toStringAsFixed(0)} m'
        : 'unknown accuracy';
    _setLocationStatus(
      'Location visible ($accuracy) — validating navigation quality',
    );
  }

  void _setLocationStatus(String status) {
    _locationStatus = status;
    _gnssDegraded = true;
  }

  String _rejectedLocationStatus(Position position, DateTime now) {
    if (!_isDisplayableGnssPosition(position, now) ||
        position.timestamp.isBefore(now.subtract(_maximumNavigationFixAge))) {
      return 'Location fix is stale or invalid — waiting for a current fix';
    }
    if (!position.hasAccuracy ||
        !position.accuracy.isFinite ||
        position.accuracy <= 0) {
      return 'Location update has no measured accuracy — retaining the latest GNSS fix';
    }
    if (position.accuracy > _maximumNavigationAccuracyM) {
      return 'Location accuracy is ${position.accuracy.toStringAsFixed(0)} m — retaining the latest GNSS fix';
    }
    return 'Location jump rejected — retaining the latest GNSS fix';
  }

  bool _hasInitialQualityFixPair(Position position, DateTime now) {
    // A native Android location callback preserves the platform's timestamp
    // and measured accuracy.  A current one is safe to use as the initial
    // origin even while stationary, where Android is allowed to repeat the
    // same timestamp instead of emitting a needless second fix.  Older cached
    // observations still need a second, strictly newer stream event.
    if (_lastAccurate != null || _predicted != null) return true;
    final age = now.difference(position.timestamp);
    if (age >= Duration.zero && age <= _gnssFixTimeout) {
      _firstInitialQualityFixTimestamp = null;
      return true;
    }
    final first = _firstInitialQualityFixTimestamp;
    if (first == null || !position.timestamp.isAfter(first)) {
      _firstInitialQualityFixTimestamp = position.timestamp;
      return false;
    }
    _firstInitialQualityFixTimestamp = null;
    return true;
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
      _firstInitialQualityFixTimestamp = null;
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
      // Do not turn an impossible neural-network extrapolation into either a
      // navigation measurement or a plausible-looking diagnostic value.
      // `NaN` is serialized as null in trip logs and rendered as rejected.
      _modelSpeedMps =
          inferred.hasPlausibleSpeed ? inferred.speedMps : double.nan;
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
    final insPropagationReady = mode == NavigationMode.deadReckoning &&
        _vehicleMotionLatch.armed &&
        !_stationary &&
        inferred != null &&
        inferred.mountCalibrated &&
        inferred.sensorsFresh &&
        !inferred.mountDegraded &&
        !inferred.motionAnomaly;
    // In GNSS-aided mode, GNSS speed is the measured reference. The CNN is
    // reserved for a valid, calibrated blackout window rather than overriding
    // a good satellite measurement with a model extrapolation.
    _speedMps = gnssReportsMotion
        ? _lastGoodGnss!.speed
        : insPropagationReady
            ? (_insSpeedFilter.propagate(
                  forwardAccelerationMps2: inferred.forwardAccelerationMps2,
                  dtSeconds: dt,
                  cnnSpeedMps: _velocityModelTrusted ? _modelSpeedMps : null,
                  cnnTrusted: _velocityModelTrusted,
                ) ??
                0.0)
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
    if (mode == NavigationMode.deadReckoning ||
        mode == NavigationMode.reacquiring) {
      _advancePrediction(
        dt,
        countDrDistance: mode == NavigationMode.deadReckoning,
      );
    }
    _setPredictionConfidence(inferred, freshFix, mode, now);
    final display = _displayPosition(mode, now);
    final actual = _actualPosition == null
        ? null
        : _LocalPosition(_actualPosition!.latitude, _actualPosition!.longitude);
    final error =
        mode == NavigationMode.acquiring || display == null || actual == null
            ? double.nan
            : _distanceMeters(display, actual);
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
      // A sensor-only startup snapshot deliberately carries no invented map
      // coordinate. The UI renders the map's honest acquisition state while
      // Developer Mode can still expose real IMU and location diagnostics.
      latitudeDeg: display?.latitude ?? double.nan,
      longitudeDeg: display?.longitude ?? double.nan,
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
    // A real, coarse bootstrap position is intentionally not a DR origin.
    // It gives the user a map pin while the quality gate waits for an
    // aid-quality GNSS observation.
    if (_gnssEnabled && _lastAccurate == null && _predicted == null) {
      _lossStarted = null;
      _reacquireStarted = null;
      return NavigationMode.acquiring;
    }
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
    if (mode == NavigationMode.acquiring) {
      final timestamp = _bootstrapPositionTimestamp;
      if (timestamp == null ||
          timestamp.isBefore(now.subtract(_maximumBootstrapFixAge))) {
        return null;
      }
      return _bootstrapPosition;
    }
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
    final receipt = _lastAcceptedGnssReceiptAt;
    if (fix == null || receipt == null) return false;
    final fixAge = now.difference(fix.timestamp);
    final heartbeatAge = now.difference(receipt);
    return fixAge >= Duration.zero &&
        fixAge <= _maximumNavigationFixAge &&
        heartbeatAge >= Duration.zero &&
        heartbeatAge <= _gnssFixTimeout;
  }

  /// A navigation-quality fix keeps the map and GNSS-aided state alive. It is
  /// deliberately broader than a fusion-quality fix: the user should see an
  /// honestly degraded position, not a false outage, while accuracy converges.
  bool _isNavigationGnssFix(Position position, DateTime now) =>
      _isDisplayableGnssPosition(position, now) &&
      position.accuracy.isFinite &&
      position.accuracy > 0 &&
      position.accuracy <= _maximumNavigationAccuracyM &&
      !position.timestamp.isBefore(now.subtract(_maximumNavigationFixAge));

  bool _isFusionQualityGnssFix(Position position, DateTime now) =>
      _isNavigationGnssFix(position, now) &&
      position.accuracy <= _maximumAidingAccuracyM;

  bool _isDisplayableGnssPosition(Position position, DateTime now) =>
      !position.isMocked &&
      position.latitude.isFinite &&
      position.longitude.isFinite &&
      position.latitude.abs() <= 90 &&
      position.longitude.abs() <= 180 &&
      !position.timestamp.isAfter(now.add(const Duration(seconds: 3))) &&
      !position.timestamp.isBefore(now.subtract(_maximumBootstrapFixAge));

  bool _isPlausibleAidingFix(Position candidate) {
    final previous = _lastGoodGnss;
    final previousTimestamp = _lastAcceptedGnssTimestamp;
    if (previous == null || previousTimestamp == null) return true;
    // GPS_PROVIDER and Android's high-accuracy provider can legitimately
    // publish the same physical epoch. Treat a position with an equal
    // timestamp as a heartbeat when it agrees spatially; only a time reversal
    // is stale. This restores the normal stationary-location behaviour from
    // the original app without accepting a teleport.
    if (candidate.timestamp.isBefore(previousTimestamp)) return false;
    final elapsedS = math.max(
      0.0,
      candidate.timestamp.difference(previousTimestamp).inMilliseconds / 1000,
    );
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

  bool _hasMeasuredSpeed(Position position) =>
      position.hasSpeed &&
      position.speed.isFinite &&
      position.speed >= 0 &&
      position.speed <= _maximumDrSpeedMps &&
      (!position.hasSpeedAccuracy ||
          (position.speedAccuracy.isFinite && position.speedAccuracy <= 5));

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

  void _setPredictionConfidence(
    VelocityEstimate? inferred,
    bool freshFix,
    NavigationMode mode,
    DateTime now,
  ) {
    if (mode == NavigationMode.acquiring) {
      _predictionConfidence = 0;
      _predictionConfidenceReason = _locationStatus;
      return;
    }
    if (mode == NavigationMode.gnssAidedIns &&
        freshFix &&
        _lastGoodGnss != null) {
      _predictionConfidence =
          (1 - _lastGoodGnss!.accuracy / _maximumAidingAccuracyM)
              .clamp(0.2, 1.0)
              .toDouble();
      _predictionConfidenceReason = _gnssDegraded
          ? 'Quality location aided with degraded accuracy (${_lastGoodGnss!.accuracy.toStringAsFixed(1)} m)'
          : 'Quality location aided (${_lastGoodGnss!.accuracy.toStringAsFixed(1)} m accuracy)';
      return;
    }
    if (inferred == null) {
      _predictionConfidence = 0;
      _predictionConfidenceReason =
          _velocityModelStartupWarning ?? 'Awaiting 2 s calibrated IMU window';
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
    // Set this before awaiting cancellation so an in-flight asynchronous
    // interpreter creation disposes itself instead of attaching after stop.
    _started = false;
    _ticker?.cancel();
    await _positionSubscription?.cancel();
    await _nativePositionSubscription?.cancel();
    await _accelerometerSubscription?.cancel();
    await _gyroscopeSubscription?.cancel();
    await _magnetometerSubscription?.cancel();
    await _velocityEstimator?.dispose();
    await _tripRecorder.stop();
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
