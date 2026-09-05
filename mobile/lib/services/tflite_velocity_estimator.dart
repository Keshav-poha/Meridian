import 'dart:async';
import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:sensors_plus/sensors_plus.dart';
import 'package:tflite_flutter/tflite_flutter.dart';

import '../domain/telemetry_snapshot.dart';
import 'imu_preprocessor.dart';
import 'velocity_model_quality.dart';

/// Runs the shared Stage 10 velocity CNN over live phone IMU data.
///
/// This class deliberately exposes only measured sensor values and inferred
/// forward speed. Stage 11's navigation coordinator adds calibration, heading,
/// NHC integration, and GNSS fusion before publishing a [TelemetrySnapshot].
class TfliteVelocityEstimator {
  static const _windowSamples = 20;
  static const _windowDuration = Duration(seconds: 2);

  TfliteVelocityEstimator._(this._interpreter, this._mean, this._std,
      this._targetMean, this._targetStd);

  final Interpreter _interpreter;
  final List<double> _mean;
  final List<double> _std;
  final double _targetMean;
  final double _targetStd;
  final List<_ImuReading> _samples = <_ImuReading>[];
  final VehicleFramePreprocessor _preprocessor = VehicleFramePreprocessor();

  Axis3 _accelerometer = const Axis3(0, 0, 0);
  Axis3 _gyroscope = const Axis3(0, 0, 0);
  Axis3 _magnetometer = const Axis3(0, 0, 0);
  DateTime? _gyroscopeTimestamp;
  DateTime? _magnetometerTimestamp;
  VehicleImuFrame? _latestVehicleFrame;
  StreamSubscription<AccelerometerEvent>? _accelerometerSubscription;
  StreamSubscription<GyroscopeEvent>? _gyroscopeSubscription;
  StreamSubscription<MagnetometerEvent>? _magnetometerSubscription;

  static Future<TfliteVelocityEstimator> create() async {
    final interpreter =
        await Interpreter.fromAsset('assets/models/velocity_cnn.tflite');
    final decoded = jsonDecode(await rootBundle.loadString(
      'assets/models/velocity_cnn.normalization.json',
    )) as Map<String, dynamic>;
    List<double> asDoubles(String key) => (decoded[key] as List<dynamic>)
        .map((value) => (value as num).toDouble())
        .toList(growable: false);
    return TfliteVelocityEstimator._(
      interpreter,
      asDoubles('feature_mean'),
      asDoubles('feature_std'),
      (decoded['target_mean'] as num).toDouble(),
      (decoded['target_std'] as num).toDouble(),
    );
  }

  /// Starts physical IMU streams. No synthetic or placeholder values are used.
  void start() {
    _gyroscopeSubscription ??= gyroscopeEventStream().listen((event) {
      _gyroscope = Axis3(event.x, event.y, event.z);
      _gyroscopeTimestamp = DateTime.now();
    });
    _magnetometerSubscription ??= magnetometerEventStream().listen((event) {
      _magnetometer = Axis3(event.x, event.y, event.z);
      _magnetometerTimestamp = DateTime.now();
    });
    _accelerometerSubscription ??= accelerometerEventStream().listen((event) {
      _accelerometer = Axis3(event.x, event.y, event.z);
      _append(DateTime.now());
    });
  }

  /// Supplies the moving-vehicle GNSS reference used to resolve mount yaw.
  void setGnssReference({
    required double speedMps,
    required double headingDeg,
    required double accuracyM,
    required DateTime timestamp,
    required bool headingReliable,
  }) {
    final now = DateTime.now();
    if (_magnetometerTimestamp == null ||
        now.difference(_magnetometerTimestamp!).abs() >
            const Duration(milliseconds: 750)) {
      return;
    }
    _preprocessor.setGnssReference(
      speedMps: speedMps,
      headingDeg: headingDeg,
      accuracyM: accuracyM,
      timestamp: timestamp,
      headingReliable: headingReliable,
    );
  }

  /// Returns a quality-scored estimate when a full two-second window is ready.
  VelocityEstimate? estimate() {
    if (_samples.length < 2 ||
        _samples.last.timestamp.difference(_samples.first.timestamp) <
            _windowDuration) {
      return null;
    }
    final newest = _samples.last.timestamp;
    final start = newest.subtract(_windowDuration);
    final input = List<List<List<double>>>.generate(
      1,
      (_) => List<List<double>>.generate(_windowSamples, (sampleIndex) {
        final target = start.add(Duration(
          microseconds: ((_windowDuration.inMicroseconds * (sampleIndex + 1)) /
                  _windowSamples)
              .round(),
        ));
        final values = _interpolate(target);
        return List<double>.generate(
          9,
          (channel) =>
              (values[channel] - _mean[channel]) /
              _std[channel].clamp(1e-4, double.infinity).toDouble(),
          growable: false,
        );
      }, growable: false),
      growable: false,
    );
    final output = List<List<double>>.filled(1, List<double>.filled(1, 0),
        growable: false);
    _interpreter.run(input, output);
    final latestFrame = _latestVehicleFrame!;
    final latest = _samples.last.timestamp;
    final now = DateTime.now();
    final sensorsFresh =
        now.difference(latest).abs() <= const Duration(milliseconds: 250) &&
            _gyroscopeTimestamp != null &&
            _magnetometerTimestamp != null &&
            now.difference(_gyroscopeTimestamp!).abs() <=
                const Duration(milliseconds: 250) &&
            now.difference(_magnetometerTimestamp!).abs() <=
                const Duration(milliseconds: 750);
    return VelocityEstimate(
      speedMps: output[0][0] * _targetStd + _targetMean,
      quality: VelocityModelQuality.fromNormalizedWindow(input[0]),
      mountCalibrated: _samples.every((sample) => sample.mountCalibrated) &&
          latestFrame.mountCalibrated,
      mountConfidence: latestFrame.mountConfidence,
      mountDegraded: latestFrame.mountDegraded,
      motionAnomaly: latestFrame.motionAnomaly,
      sensorsFresh: sensorsFresh,
      vehicleYawRateRps: latestFrame.gyroscope.z,
    );
  }

  void _append(DateTime timestamp) {
    // Never construct a two-second input by holding a delayed gyro or
    // magnetometer sample across fresh accelerometer callbacks. Clearing the
    // window forces a new contiguous, timestamp-compatible interval.
    if (_gyroscopeTimestamp == null ||
        _magnetometerTimestamp == null ||
        timestamp.difference(_gyroscopeTimestamp!).abs() >
            const Duration(milliseconds: 250) ||
        timestamp.difference(_magnetometerTimestamp!).abs() >
            const Duration(milliseconds: 750)) {
      _samples.clear();
      _latestVehicleFrame = null;
      return;
    }
    final vehicleFrame = _preprocessor.update(
      accelerometer: _accelerometer,
      gyroscope: _gyroscope,
      magnetometer: _magnetometer,
      timestamp: timestamp,
    );
    _latestVehicleFrame = vehicleFrame;
    _samples.add(_ImuReading(
      timestamp,
      vehicleFrame.modelValues,
      vehicleFrame.mountCalibrated,
    ));
    final cutoff =
        timestamp.subtract(_windowDuration + const Duration(milliseconds: 100));
    _samples.removeWhere((sample) => sample.timestamp.isBefore(cutoff));
  }

  List<double> _interpolate(DateTime target) {
    for (var index = 1; index < _samples.length; index++) {
      final left = _samples[index - 1];
      final right = _samples[index];
      if (!target.isAfter(right.timestamp)) {
        final span = right.timestamp.difference(left.timestamp).inMicroseconds;
        final fraction = span == 0
            ? 0.0
            : target.difference(left.timestamp).inMicroseconds / span;
        return List<double>.generate(
            9,
            (channel) =>
                left.values[channel] +
                (right.values[channel] - left.values[channel]) * fraction);
      }
    }
    return _samples.last.values;
  }

  Future<void> dispose() async {
    await _accelerometerSubscription?.cancel();
    await _gyroscopeSubscription?.cancel();
    await _magnetometerSubscription?.cancel();
    _interpreter.close();
  }
}

class _ImuReading {
  const _ImuReading(this.timestamp, this.values, this.mountCalibrated);
  final DateTime timestamp;
  final List<double> values;
  final bool mountCalibrated;
}

class VelocityEstimate {
  static const maximumPlausibleVehicleSpeedMps = 45.0;

  const VelocityEstimate({
    required this.speedMps,
    required this.quality,
    required this.mountCalibrated,
    required this.mountConfidence,
    required this.mountDegraded,
    required this.motionAnomaly,
    required this.sensorsFresh,
    required this.vehicleYawRateRps,
  });

  final double speedMps;
  final VelocityModelQuality quality;
  final bool mountCalibrated;
  final double mountConfidence;
  final bool mountDegraded;
  final bool motionAnomaly;
  final bool sensorsFresh;
  final double vehicleYawRateRps;

  bool get hasPlausibleSpeed =>
      speedMps.isFinite &&
      speedMps >= 0 &&
      speedMps <= maximumPlausibleVehicleSpeedMps;

  double get confidence =>
      quality.confidence *
      (mountCalibrated ? mountConfidence : 0) *
      (sensorsFresh && !motionAnomaly && !mountDegraded ? 1 : 0);

  bool get isTrusted =>
      hasPlausibleSpeed &&
      quality.isInDistribution &&
      mountCalibrated &&
      sensorsFresh &&
      !motionAnomaly &&
      !mountDegraded &&
      confidence >= 0.6;
}
