import 'dart:math' as math;

import '../domain/telemetry_snapshot.dart';

/// Rejects a velocity-CNN output while the phone is physically at rest.
///
/// The driving-only IO-VNBD model has no parked-phone examples, so its output
/// must not by itself start a trajectory when gravity is stable and angular
/// rate is negligible. GNSS speed, when present, vetoes this gate in the live
/// engine so smooth constant-speed driving remains navigable.
class MotionGate {
  MotionGate({this.requiredStillSamples = 5});

  static const _gravityToleranceMps2 = 0.35;
  static const _angularRateToleranceRps = 0.12;

  final int requiredStillSamples;
  int _stillSamples = 0;

  bool update({
    required Axis3 accelerometer,
    required Axis3 gyroscope,
    required bool gnssReportsMotion,
  }) {
    final accelerationMagnitude = _magnitude(accelerometer);
    final angularRateMagnitude = _magnitude(gyroscope);
    final gravityStable =
        (accelerationMagnitude - 9.80665).abs() <= _gravityToleranceMps2;
    final angularlyStill = angularRateMagnitude <= _angularRateToleranceRps;
    if (gravityStable && angularlyStill && !gnssReportsMotion) {
      _stillSamples =
          (_stillSamples + 1).clamp(0, requiredStillSamples).toInt();
    } else {
      _stillSamples = 0;
    }
    return _stillSamples >= requiredStillSamples;
  }

  static double _magnitude(Axis3 value) => math.sqrt(
        value.x * value.x + value.y * value.y + value.z * value.z,
      );
}

/// Remembers that GNSS has observed actual vehicle motion before an outage.
///
/// A local IMU alone cannot tell a hand-held phone shake from a moving car.
/// The latch therefore prevents an unarmed, parked phone from turning a
/// vehicle-trained velocity-CNN response into a navigation speed. Once GNSS
/// confirms movement, the state is retained across a deliberate GNSS outage
/// so dead reckoning can continue without an external speedometer.
class VehicleMotionLatch {
  bool _armed = false;

  bool update({
    required bool gnssEnabled,
    required bool gnssFixFresh,
    required bool gnssReportsMotion,
  }) {
    if (gnssReportsMotion) {
      _armed = true;
    } else if (gnssEnabled && gnssFixFresh) {
      _armed = false;
    }
    return _armed;
  }

  bool get armed => _armed;
}
