import 'dart:math' as math;

import '../domain/telemetry_snapshot.dart';

/// Rejects a velocity-CNN output while the phone is physically at rest.
///
/// The driving-only IO-VNBD model has no parked-phone examples, so its output
/// must not by itself start a trajectory when gravity is stable and angular
/// rate is negligible. GNSS speed, when present, vetoes this gate in the live
/// engine so smooth constant-speed driving remains navigable. A vehicle that
/// was already GNSS-confirmed as moving keeps that state during a GNSS
/// blackout: a phone IMU cannot distinguish a stopped vehicle from one that is
/// cruising steadily using only near-gravity acceleration and a low yaw rate.
class MotionGate {
  MotionGate({this.requiredStillSamples = 10, this.shockCooldownSamples = 20});

  static const _gravityToleranceMps2 = 0.35;
  static const _angularRateToleranceRps = 0.12;
  static const _shockAccelerationMps2 = 5.0;
  static const _shockAngularRateRps = 2.5;

  final int requiredStillSamples;
  final int shockCooldownSamples;
  int _stillSamples = 0;
  int _shockSamplesRemaining = 0;

  bool get inShockCooldown => _shockSamplesRemaining > 0;

  bool update({
    required Axis3 accelerometer,
    required Axis3 gyroscope,
    required bool gnssReportsMotion,
    required bool vehicleMotionArmed,
    bool externalMotionAnomaly = false,
    double? currentSpeedMps,
  }) {
    final accelerationMagnitude = _magnitude(accelerometer);
    final angularRateMagnitude = _magnitude(gyroscope);
    final shock = externalMotionAnomaly ||
        (accelerationMagnitude - 9.80665).abs() > _shockAccelerationMps2 ||
        angularRateMagnitude > _shockAngularRateRps;
    if (shock) {
      _shockSamplesRemaining = shockCooldownSamples;
      _stillSamples = 0;
      return false;
    }
    if (_shockSamplesRemaining > 0) {
      _shockSamplesRemaining--;
      _stillSamples = 0;
      return false;
    }
    final gravityStable =
        (accelerationMagnitude - 9.80665).abs() <= _gravityToleranceMps2;
    final angularlyStill = angularRateMagnitude <= _angularRateToleranceRps;
    // Smooth constant-speed cruising without IMU jerk must not be falsely frozen.
    // However, when estimated speed drops near zero, allow stillness to trigger ZUPT.
    final cruising = vehicleMotionArmed &&
        (currentSpeedMps == null || currentSpeedMps > 0.4);
    if (gnssReportsMotion || cruising) {
      _stillSamples = 0;
      return false;
    }
    if (gravityStable && angularlyStill) {
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
  VehicleMotionLatch({
    this.requiredMovingFixes = 3,
    this.requiredStoppedFixes = 3,
  });

  final int requiredMovingFixes;
  final int requiredStoppedFixes;
  bool _armed = false;
  int _movingEvidence = 0;
  int _stoppedEvidence = 0;

  bool update({
    required bool gnssEnabled,
    required bool gnssFixFresh,
    required bool gnssReportsMotion,
  }) {
    if (gnssReportsMotion) {
      _movingEvidence =
          (_movingEvidence + 1).clamp(0, requiredMovingFixes).toInt();
      _stoppedEvidence = 0;
      if (_movingEvidence >= requiredMovingFixes) _armed = true;
    } else if (gnssEnabled && gnssFixFresh) {
      _stoppedEvidence =
          (_stoppedEvidence + 1).clamp(0, requiredStoppedFixes).toInt();
      _movingEvidence = 0;
      if (_stoppedEvidence >= requiredStoppedFixes) _armed = false;
    }
    return _armed;
  }

  bool get armed => _armed;

  void disarm() {
    _armed = false;
    _movingEvidence = 0;
  }
}

/// Requires a newly timestamped, quality-checked GNSS fix after an aiding
/// outage before the navigation engine is allowed to begin its visual blend.
///
/// A fused-location provider can deliver a callback containing an old cached
/// fix just after the user re-enables GNSS.  Treating that callback as a
/// recovery would pull the display back toward the pre-outage position.  This
/// tiny gate is deliberately independent of the location provider so its
/// timestamp rule can be unit-tested.
class GnssRecoveryGate {
  DateTime? _minimumAcceptedTimestamp;

  bool get awaitingFreshFix => _minimumAcceptedTimestamp != null;

  void requireFixAfter(DateTime outageOrEnableTime) {
    _minimumAcceptedTimestamp = outageOrEnableTime;
  }

  bool accept(DateTime fixTimestamp) {
    final minimum = _minimumAcceptedTimestamp;
    if (minimum != null && !fixTimestamp.isAfter(minimum)) return false;
    _minimumAcceptedTimestamp = null;
    return true;
  }
}
