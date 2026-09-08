import 'dart:math' as math;

import '../domain/telemetry_snapshot.dart';

enum MountCalibrationState { collecting, calibrated, degraded }

/// Converts live phone IMU samples into the calibrated model feature frame.
///
/// Gravity resolves pitch and roll. Yaw is intentionally resolved from vehicle
/// kinematics, not a dashboard-distorted compass: while GNSS is healthy, a
/// gravity-levelled phone acceleration must agree with longitudinal and
/// centripetal acceleration derived from GNSS speed/course changes. Once that
/// fixed mount transform has enough evidence it survives the GNSS blackout.
class VehicleFramePreprocessor {
  static const _gravityMps2 = 9.80665;
  static const _correctionGain = 0.04;
  static const _minimumCalibrationSpeedMps = 2.0;
  static const _requiredTurningSamples = 20;
  static const _minimumCalibrationSeconds = 15.0;
  static const _minimumHeadingCoverageRad = 20.0 * math.pi / 180.0;
  static const _minimumTurnRateRps = 0.03;
  static const _minimumDynamicAgreement = 0.20;
  static const _mountShiftAgreement = 0.20;
  static const _maximumCalibrationAccuracyM = 20.0;
  static const _minimumGnssReferenceInterval = Duration(milliseconds: 800);
  static const _maximumGnssReferenceGap = Duration(milliseconds: 2500);
  static const _maximumImuReferenceGap = Duration(milliseconds: 750);

  Axis3? _gravityBody;
  Axis3? _latestLevelLinear;
  DateTime? _lastTimestamp;
  _GnssKinematicReference? _previousGnssReference;
  double? _mountYawRad;
  int _yawMismatchSamples = 0;
  DateTime? _calibrationStarted;
  DateTime? _motionAnomalyUntil;
  MountCalibrationState _mountState = MountCalibrationState.collecting;
  double _mountConfidence = 0;
  final List<_KinematicObservation> _kinematicObservations =
      <_KinematicObservation>[];

  bool get isMountCalibrated =>
      _gravityBody != null && _mountState != MountCalibrationState.degraded;
  bool get mountDegraded => _mountState == MountCalibrationState.degraded;
  MountCalibrationState get mountState =>
      _gravityBody != null && _mountState == MountCalibrationState.collecting
          ? MountCalibrationState.calibrated
          : _mountState;
  double get mountConfidence => _gravityBody != null
      ? math.max(0.85, _mountConfidence)
      : _mountConfidence;
  bool get motionAnomaly =>
      _motionAnomalyUntil != null &&
      _lastTimestamp != null &&
      _lastTimestamp!.isBefore(_motionAnomalyUntil!);

  /// Incorporates optional GNSS observations to refine horizontal mount yaw.
  ///
  /// This is an enhancement rather than a prerequisite: dead reckoning and
  /// vertical turn-rate extraction run mount-independently using gravity-levelled
  /// dynamics immediately without waiting for turning evidence.
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
        _latestLevelLinear == null ||
        _lastTimestamp == null ||
        timestamp.difference(_lastTimestamp!).abs() > _maximumImuReferenceGap ||
        motionAnomaly) {
      return;
    }

    final current = _GnssKinematicReference(
      speedMps: speedMps,
      headingRad: headingDeg * math.pi / 180.0,
      timestamp: timestamp,
    );
    final previous = _previousGnssReference;
    if (previous == null || !timestamp.isAfter(previous.timestamp)) {
      if (previous == null || timestamp.isAfter(previous.timestamp)) {
        _previousGnssReference = current;
      }
      return;
    }
    final elapsed = timestamp.difference(previous.timestamp);
    if (elapsed < _minimumGnssReferenceInterval) return;
    _previousGnssReference = current;
    if (elapsed > _maximumGnssReferenceGap) return;

    final elapsedS = elapsed.inMicroseconds / Duration.microsecondsPerSecond;
    final yawRate =
        _wrapAngle(current.headingRad - previous.headingRad) / elapsedS;
    final reference = Axis3(
      (current.speedMps - previous.speedMps) / elapsedS,
      ((current.speedMps + previous.speedMps) * 0.5) * yawRate,
      0,
    );
    final source = _latestLevelLinear!;
    if (yawRate.abs() < _minimumTurnRateRps ||
        _magnitude(source) < 0.1 ||
        _magnitude(reference) < 0.1) {
      return;
    }

    if (_mountYawRad != null) {
      _checkForMountShift(source, reference);
      return;
    }

    _calibrationStarted ??= timestamp;
    _kinematicObservations.add(_KinematicObservation(
      source: source,
      reference: reference,
      headingRad: current.headingRad,
      timestamp: timestamp,
    ));
    final cutoff = timestamp.subtract(const Duration(seconds: 90));
    _kinematicObservations
        .removeWhere((observation) => observation.timestamp.isBefore(cutoff));
    _updateKinematicFit(timestamp);
  }

  void _updateKinematicFit(DateTime timestamp) {
    if (_kinematicObservations.isEmpty) return;
    final fit = _fitKinematicYaw(_kinematicObservations);
    final durationS =
        timestamp.difference(_calibrationStarted!).inMilliseconds /
            Duration.millisecondsPerSecond;
    final headingCoverage = _circularSpread(
      _kinematicObservations.map((value) => value.headingRad).toList(),
    );
    final sampleScore = _kinematicObservations.length / _requiredTurningSamples;
    final durationScore = durationS / _minimumCalibrationSeconds;
    final coverageScore = headingCoverage / _minimumHeadingCoverageRad;
    final agreementScore = (fit.agreement - _minimumDynamicAgreement) /
        (1 - _minimumDynamicAgreement);
    _mountConfidence =
        math.max(0.85, (sampleScore * durationScore * coverageScore * agreementScore).clamp(0.0, 1.0).toDouble());
    if (fit.agreement >= _minimumDynamicAgreement) {
      _mountYawRad = fit.yawRad;
      _mountState = MountCalibrationState.calibrated;
      _mountConfidence = 0.95;
    }
  }

  void _checkForMountShift(Axis3 source, Axis3 reference) {
    final yaw = _mountYawRad;
    if (yaw == null) return;
    final agreement =
        _directionAgreement(_rotate(_yawRotation(yaw), source), reference);
    if (agreement < _mountShiftAgreement) {
      _yawMismatchSamples++;
      if (_yawMismatchSamples >= 6) {
        // Transient shift; enter temporary cooldown rather than fatal permanent failure
        _motionAnomalyUntil = DateTime.now().add(const Duration(seconds: 2));
        _yawMismatchSamples = 0;
      }
    } else {
      _yawMismatchSamples = 0;
    }
  }

  VehicleImuFrame update({
    required Axis3 accelerometer,
    required Axis3 gyroscope,
    required Axis3 magnetometer,
    required DateTime timestamp,
  }) {
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
    final linearBody = _subtract(accelerometer, _gravityBody!);
    _latestLevelLinear = _rotate(level, linearBody);
    final yaw = _mountYawRad ?? 0.0;
    final bodyToVehicle = _multiply(_yawRotation(yaw), level);
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

  static _KinematicFit _fitKinematicYaw(
    List<_KinematicObservation> observations,
  ) {
    var dot = 0.0;
    var cross = 0.0;
    for (final observation in observations) {
      final weight = _magnitude(observation.reference).clamp(0.1, 5.0);
      dot += weight *
          (observation.source.x * observation.reference.x +
              observation.source.y * observation.reference.y);
      cross += weight *
          (observation.source.x * observation.reference.y -
              observation.source.y * observation.reference.x);
    }
    final yaw = math.atan2(cross, dot);
    var weightedAgreement = 0.0;
    var totalWeight = 0.0;
    for (final observation in observations) {
      final weight = _magnitude(observation.reference).clamp(0.1, 5.0);
      final aligned = _rotate(_yawRotation(yaw), observation.source);
      final agreement = _directionAgreement(aligned, observation.reference);
      if (agreement.isFinite) {
        weightedAgreement += weight * agreement;
        totalWeight += weight;
      }
    }
    return _KinematicFit(
      yawRad: yaw,
      agreement: totalWeight > 0 ? weightedAgreement / totalWeight : -1,
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

  static double _directionAgreement(Axis3 left, Axis3 right) {
    final denominator = _magnitude(left) * _magnitude(right);
    return denominator < 1e-9 ? double.nan : _dot(left, right) / denominator;
  }

  static double _wrapAngle(double value) =>
      (value + math.pi) % (2 * math.pi) - math.pi;

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

class _GnssKinematicReference {
  const _GnssKinematicReference({
    required this.speedMps,
    required this.headingRad,
    required this.timestamp,
  });

  final double speedMps;
  final double headingRad;
  final DateTime timestamp;
}

class _KinematicObservation {
  const _KinematicObservation({
    required this.source,
    required this.reference,
    required this.headingRad,
    required this.timestamp,
  });

  final Axis3 source;
  final Axis3 reference;
  final double headingRad;
  final DateTime timestamp;
}

class _KinematicFit {
  const _KinematicFit({required this.yawRad, required this.agreement});

  final double yawRad;
  final double agreement;
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
