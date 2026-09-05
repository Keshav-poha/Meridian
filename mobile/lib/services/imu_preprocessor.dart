import 'dart:math' as math;

import '../domain/telemetry_snapshot.dart';

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
  static const _requiredCourseSamples = 5;

  Axis3? _gravityBody;
  Axis3 _magnetometer = const Axis3(0, 0, 0);
  DateTime? _lastTimestamp;
  double? _mountYawRad;
  int _courseSamples = 0;

  bool get isMountCalibrated => _courseSamples >= _requiredCourseSamples;

  /// Incorporates a valid GNSS course only while the vehicle is moving.
  void setGnssReference(
      {required double speedMps, required double headingDeg}) {
    if (!speedMps.isFinite ||
        speedMps < _minimumCalibrationSpeedMps ||
        !headingDeg.isFinite ||
        headingDeg < 0 ||
        _gravityBody == null ||
        _magnitude(_magnetometer) < 1e-6) {
      return;
    }
    final levelMagnetic = _rotate(_levelRotation(_gravityBody!), _magnetometer);
    final phoneMagneticCourse = math.atan2(levelMagnetic.y, levelMagnetic.x);
    final vehicleCourse = headingDeg * math.pi / 180;
    final candidateYaw = _wrapAngle(vehicleCourse - phoneMagneticCourse);
    _mountYawRad = _mountYawRad == null
        ? candidateYaw
        : _circularBlend(_mountYawRad!, candidateYaw, 0.15);
    _courseSamples = math.min(_courseSamples + 1, _requiredCourseSamples);
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
    final linearVehicle = _rotate(bodyToVehicle, linearBody);
    final gyroVehicle = _rotate(bodyToVehicle, gyroscope);
    final magneticVehicle = _unit(_rotate(bodyToVehicle, magnetometer));
    return VehicleImuFrame(
      linearAcceleration: linearVehicle,
      gyroscope: gyroVehicle,
      magneticDirection: magneticVehicle,
      gravityBody: _gravityBody!,
      mountCalibrated: isMountCalibrated,
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
    if ((accelerationMagnitude - _gravityMps2).abs() > 1.5) return propagated;
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
  static double _circularBlend(double from, double to, double fraction) =>
      _wrapAngle(from + _wrapAngle(to - from) * fraction);
}

class VehicleImuFrame {
  const VehicleImuFrame({
    required this.linearAcceleration,
    required this.gyroscope,
    required this.magneticDirection,
    required this.gravityBody,
    required this.mountCalibrated,
  });

  final Axis3 linearAcceleration;
  final Axis3 gyroscope;
  final Axis3 magneticDirection;
  final Axis3 gravityBody;
  final bool mountCalibrated;

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
