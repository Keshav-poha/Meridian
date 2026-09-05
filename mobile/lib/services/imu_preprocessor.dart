import 'dart:math' as math;

import '../domain/telemetry_snapshot.dart';

enum MountCalibrationState { collecting, calibrated, degraded }

/// Converts live phone IMU samples into the calibrated model feature frame.
///
/// Gravity is estimated by a complementary filter: gyroscope propagation keeps
/// the estimate responsive during rotation, while accelerometer correction
/// prevents gyro drift. GNSS course during actual motion resolves the final
/// phone-mount yaw that gravity alone cannot observe.
class VehicleFramePreprocessor {
  static const _gravityMps2 = 9.80665;
  static const _correctionGain = 0.04;
  static const _minimumCalibrationSpeedMps = 2.0;
  static const _requiredCourseSamples = 20;
  static const _minimumCalibrationSeconds = 15.0;
  static const _minimumHeadingCoverageRad = 20.0 * math.pi / 180.0;
  static const _maximumYawResidualRad = 12.0 * math.pi / 180.0;
  static const _mountShiftResidualRad = 25.0 * math.pi / 180.0;
  static const _maximumCalibrationAccuracyM = 20.0;

  Axis3? _gravityBody;
  Axis3 _magnetometer = const Axis3(0, 0, 0);
  DateTime? _lastTimestamp;
  double? _mountYawRad;
  int _courseSamples = 0;
  int _yawMismatchSamples = 0;
  DateTime? _calibrationStarted;
  DateTime? _motionAnomalyUntil;
  MountCalibrationState _mountState = MountCalibrationState.collecting;
  double _mountConfidence = 0;
  final List<_CourseObservation> _courseObservations = <_CourseObservation>[];

  bool get isMountCalibrated => _mountState == MountCalibrationState.calibrated;
  bool get mountDegraded => _mountState == MountCalibrationState.degraded;
  MountCalibrationState get mountState => _mountState;
  double get mountConfidence => _mountConfidence;
  bool get motionAnomaly =>
      _motionAnomalyUntil != null &&
      _lastTimestamp != null &&
      _lastTimestamp!.isBefore(_motionAnomalyUntil!);

  /// Incorporates a quality-gated GNSS course while the vehicle is moving.
  ///
  /// A phone cannot obtain a trustworthy mount yaw from five lucky location
  /// callbacks. The accepted yaw must be consistent across time and course
  /// coverage. Once a mount is accepted, persistent disagreement degrades it
  /// instead of silently replacing the transform mid-drive.
  void setGnssReference({
    required double speedMps,
    required double headingDeg,
    required double accuracyM,
    required DateTime timestamp,
    bool headingReliable = true,
  }) {
    if (!speedMps.isFinite ||
        speedMps < _minimumCalibrationSpeedMps ||
        !headingDeg.isFinite ||
        headingDeg < 0 ||
        !accuracyM.isFinite ||
        accuracyM <= 0 ||
        accuracyM > _maximumCalibrationAccuracyM ||
        !headingReliable ||
        _gravityBody == null ||
        _magnitude(_magnetometer) < 15.0 ||
        _magnitude(_magnetometer) > 90.0 ||
        motionAnomaly) {
      return;
    }
    final levelMagnetic = _rotate(_levelRotation(_gravityBody!), _magnetometer);
    final phoneMagneticCourse = math.atan2(levelMagnetic.y, levelMagnetic.x);
    final vehicleCourse = headingDeg * math.pi / 180;
    final candidateYaw = _wrapAngle(vehicleCourse - phoneMagneticCourse);

    if (isMountCalibrated) {
      final mismatch = _wrapAngle(candidateYaw - _mountYawRad!).abs();
      _yawMismatchSamples =
          mismatch > _mountShiftResidualRad ? _yawMismatchSamples + 1 : 0;
      if (_yawMismatchSamples >= 4) {
        _mountState = MountCalibrationState.degraded;
        _mountConfidence = 0;
      }
      return;
    }
    // A degraded mount must be deliberately restarted/reseated. Continuing
    // with the old transform is less safe than temporarily withholding DR.
    if (mountDegraded) return;

    _calibrationStarted ??= timestamp;
    _courseObservations.add(_CourseObservation(
      yawRad: candidateYaw,
      headingRad: vehicleCourse,
      timestamp: timestamp,
    ));
    final cutoff = timestamp.subtract(const Duration(seconds: 60));
    _courseObservations
        .removeWhere((observation) => observation.timestamp.isBefore(cutoff));
    _courseSamples =
        math.min(_courseObservations.length, _requiredCourseSamples);
    final yawMean =
        _circularMean(_courseObservations.map((value) => value.yawRad));
    final yawResidual =
        _circularRms(_courseObservations.map((value) => value.yawRad), yawMean);
    final durationS =
        timestamp.difference(_calibrationStarted!).inMilliseconds /
            Duration.millisecondsPerSecond;
    final headingCoverage = _circularSpread(
        _courseObservations.map((value) => value.headingRad).toList());
    final sampleScore = _courseSamples / _requiredCourseSamples;
    final durationScore = durationS / _minimumCalibrationSeconds;
    final coverageScore = headingCoverage / _minimumHeadingCoverageRad;
    final consistencyScore = 1 - yawResidual / _maximumYawResidualRad;
    _mountConfidence =
        (sampleScore * durationScore * coverageScore * consistencyScore)
            .clamp(0.0, 1.0)
            .toDouble();
    if (_courseSamples >= _requiredCourseSamples &&
        durationS >= _minimumCalibrationSeconds &&
        headingCoverage >= _minimumHeadingCoverageRad &&
        yawResidual <= _maximumYawResidualRad) {
      _mountYawRad = yawMean;
      _mountState = MountCalibrationState.calibrated;
      _mountConfidence = math.max(0.7, _mountConfidence);
    }
  }

  VehicleImuFrame update({
    required Axis3 accelerometer,
    required Axis3 gyroscope,
    required Axis3 magnetometer,
    required DateTime timestamp,
  }) {
    _magnetometer = magnetometer;
    final dt = _lastTimestamp == null
        ? 0.0
        : math.min(
            0.1,
            math.max(
              0.0,
              timestamp.difference(_lastTimestamp!).inMicroseconds / 1e6,
            ),
          );
    _lastTimestamp = timestamp;
    _gravityBody = _updateGravity(
      previous: _gravityBody,
      accelerometer: accelerometer,
      gyroscope: gyroscope,
      dt: dt,
    );

    final level = _levelRotation(_gravityBody!);
    final yaw = _mountYawRad ?? 0.0;
    final bodyToVehicle = _multiply(_yawRotation(yaw), level);
    final linearBody = _subtract(accelerometer, _gravityBody!);
    if (_magnitude(linearBody) > 7.0 || _magnitude(gyroscope) > 2.5) {
      _motionAnomalyUntil = timestamp.add(const Duration(seconds: 2));
    }
    final linearVehicle = _rotate(bodyToVehicle, linearBody);
    final gyroVehicle = _rotate(bodyToVehicle, gyroscope);
    final magneticVehicle = _unit(_rotate(bodyToVehicle, magnetometer));
    return VehicleImuFrame(
      linearAcceleration: linearVehicle,
      gyroscope: gyroVehicle,
      magneticDirection: magneticVehicle,
      gravityBody: _gravityBody!,
      mountCalibrated: isMountCalibrated,
      mountDegraded: mountDegraded,
      mountConfidence: mountConfidence,
      motionAnomaly: motionAnomaly,
    );
  }

  static Axis3 _updateGravity({
    required Axis3? previous,
    required Axis3 accelerometer,
    required Axis3 gyroscope,
    required double dt,
  }) {
    if (previous == null) return _withMagnitude(accelerometer, _gravityMps2);
    final propagated = _withMagnitude(
      _add(previous, _scale(_cross(previous, gyroscope), dt)),
      _gravityMps2,
    );
    final accelerationMagnitude = _magnitude(accelerometer);
    if ((accelerationMagnitude - _gravityMps2).abs() > 0.75 ||
        _magnitude(gyroscope) > 0.35) {
      return propagated;
    }
    final correction = _withMagnitude(accelerometer, _gravityMps2);
    return _withMagnitude(
      _add(
        _scale(propagated, 1 - _correctionGain),
        _scale(correction, _correctionGain),
      ),
      _gravityMps2,
    );
  }

  static List<List<double>> _levelRotation(Axis3 gravityBody) =>
      _align(gravityBody, const Axis3(0, 0, _gravityMps2));

  static List<List<double>> _yawRotation(double yaw) {
    final cosine = math.cos(yaw);
    final sine = math.sin(yaw);
    return <List<double>>[
      <double>[cosine, -sine, 0],
      <double>[sine, cosine, 0],
      <double>[0, 0, 1],
    ];
  }

  static List<List<double>> _align(Axis3 source, Axis3 target) {
    final from = _unit(source);
    final to = _unit(target);
    final cross = _cross(from, to);
    final sine = _magnitude(cross);
    final cosine = _dot(from, to).clamp(-1.0, 1.0).toDouble();
    if (sine < 1e-9) {
      return cosine >= 0
          ? _identity()
          : <List<double>>[
              <double>[-1, 0, 0],
              <double>[0, -1, 0],
              <double>[0, 0, 1],
            ];
    }
    final skew = <List<double>>[
      <double>[0, -cross.z, cross.y],
      <double>[cross.z, 0, -cross.x],
      <double>[-cross.y, cross.x, 0],
    ];
    final skewSquared = _multiply(skew, skew);
    final factor = (1 - cosine) / (sine * sine);
    return List<List<double>>.generate(
      3,
      (row) => List<double>.generate(
        3,
        (column) =>
            (row == column ? 1.0 : 0.0) +
            skew[row][column] +
            skewSquared[row][column] * factor,
      ),
    );
  }

  static List<List<double>> _identity() => <List<double>>[
        <double>[1, 0, 0],
        <double>[0, 1, 0],
        <double>[0, 0, 1],
      ];

  static List<List<double>> _multiply(
    List<List<double>> left,
    List<List<double>> right,
  ) =>
      List<List<double>>.generate(
        3,
        (row) => List<double>.generate(
          3,
          (column) => List<double>.generate(
            3,
            (index) => left[row][index] * right[index][column],
          ).reduce((sum, value) => sum + value),
        ),
      );

  static Axis3 _rotate(List<List<double>> matrix, Axis3 vector) => Axis3(
        matrix[0][0] * vector.x +
            matrix[0][1] * vector.y +
            matrix[0][2] * vector.z,
        matrix[1][0] * vector.x +
            matrix[1][1] * vector.y +
            matrix[1][2] * vector.z,
        matrix[2][0] * vector.x +
            matrix[2][1] * vector.y +
            matrix[2][2] * vector.z,
      );

  static Axis3 _add(Axis3 left, Axis3 right) =>
      Axis3(left.x + right.x, left.y + right.y, left.z + right.z);
  static Axis3 _subtract(Axis3 left, Axis3 right) =>
      Axis3(left.x - right.x, left.y - right.y, left.z - right.z);
  static Axis3 _scale(Axis3 value, double factor) =>
      Axis3(value.x * factor, value.y * factor, value.z * factor);
  static Axis3 _cross(Axis3 left, Axis3 right) => Axis3(
        left.y * right.z - left.z * right.y,
        left.z * right.x - left.x * right.z,
        left.x * right.y - left.y * right.x,
      );
  static double _dot(Axis3 left, Axis3 right) =>
      left.x * right.x + left.y * right.y + left.z * right.z;
  static double _magnitude(Axis3 value) => math.sqrt(_dot(value, value));
  static Axis3 _unit(Axis3 value) => _withMagnitude(value, 1.0);
  static Axis3 _withMagnitude(Axis3 value, double magnitude) {
    final norm = _magnitude(value);
    return norm < 1e-9 ? const Axis3(0, 0, 0) : _scale(value, magnitude / norm);
  }

  static double _wrapAngle(double value) =>
      (value + math.pi) % (2 * math.pi) - math.pi;
  static double _circularMean(Iterable<double> values) {
    var sine = 0.0;
    var cosine = 0.0;
    for (final value in values) {
      sine += math.sin(value);
      cosine += math.cos(value);
    }
    return math.atan2(sine, cosine);
  }

  static double _circularRms(Iterable<double> values, double mean) {
    final residuals =
        values.map((value) => _wrapAngle(value - mean)).toList(growable: false);
    if (residuals.isEmpty) return double.infinity;
    return math.sqrt(
        residuals.map((value) => value * value).reduce((a, b) => a + b) /
            residuals.length);
  }

  static double _circularSpread(List<double> values) {
    var spread = 0.0;
    for (var first = 0; first < values.length; first++) {
      for (var second = first + 1; second < values.length; second++) {
        spread =
            math.max(spread, _wrapAngle(values[first] - values[second]).abs());
      }
    }
    return spread;
  }
}

class _CourseObservation {
  const _CourseObservation({
    required this.yawRad,
    required this.headingRad,
    required this.timestamp,
  });
  final double yawRad;
  final double headingRad;
  final DateTime timestamp;
}

class VehicleImuFrame {
  const VehicleImuFrame({
    required this.linearAcceleration,
    required this.gyroscope,
    required this.magneticDirection,
    required this.gravityBody,
    required this.mountCalibrated,
    required this.mountDegraded,
    required this.mountConfidence,
    required this.motionAnomaly,
  });

  final Axis3 linearAcceleration;
  final Axis3 gyroscope;
  final Axis3 magneticDirection;
  final Axis3 gravityBody;
  final bool mountCalibrated;
  final bool mountDegraded;
  final double mountConfidence;
  final bool motionAnomaly;

  List<double> get modelValues => <double>[
        linearAcceleration.x,
        linearAcceleration.y,
        linearAcceleration.z,
        gyroscope.x,
        gyroscope.y,
        gyroscope.z,
        magneticDirection.x,
        magneticDirection.y,
        magneticDirection.z,
      ];
}
