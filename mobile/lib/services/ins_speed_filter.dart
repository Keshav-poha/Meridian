import 'dart:math' as math;

/// Stateful GNSS-anchored INS speed propagation for a short GNSS blackout.
///
/// A two-second IMU CNN cannot physically observe absolute speed during a
/// perfectly steady cruise. The filter therefore starts from the last measured
/// GNSS speed and integrates gravity-compensated vehicle-forward acceleration.
/// The CNN can correct only the *change* it observes between consecutive
/// overlapping windows; its absolute output is never allowed to pull the
/// state toward an arbitrary cruise speed. This prevents a constant
/// out-of-domain model window from turning 12 m/s into 55 m/s over time.
class InsSpeedFilter {
  static const maximumSpeedMps = 45.0;
  static const _maximumAccelerationMps2 = 8.0;
  static const _maximumModelAccelerationMps2 = 4.0;
  static const _modelResidualGain = 0.12;

  double? _speedMps;
  double? _lastCnnSpeedMps;

  double? get speedMps => _speedMps;

  void reset() {
    _speedMps = null;
    _lastCnnSpeedMps = null;
  }

  /// Clamps speed to zero when stationary (Zero-Velocity Update).
  void setZeroVelocity() {
    _speedMps = 0.0;
    _lastCnnSpeedMps = null;
  }

  void anchorGnssSpeed(double measuredSpeedMps) {
    if (!measuredSpeedMps.isFinite ||
        measuredSpeedMps < 0 ||
        measuredSpeedMps > maximumSpeedMps) {
      return;
    }
    _speedMps = measuredSpeedMps;
    // GNSS may have been available for minutes since the last propagation;
    // do not form a false CNN derivative across that gap on the next outage.
    _lastCnnSpeedMps = null;
  }

  double? propagate({
    required double forwardAccelerationMps2,
    required double dtSeconds,
    double? cnnSpeedMps,
    required bool cnnTrusted,
  }) {
    if (!dtSeconds.isFinite || dtSeconds <= 0 || dtSeconds > 0.25) {
      return _speedMps;
    }
    // Allow standalone propagation even when GNSS is disabled or unanchored
    final previous = _speedMps ??
        (cnnTrusted && cnnSpeedMps != null && cnnSpeedMps.isFinite
            ? cnnSpeedMps
            : 0.0);
    final usableAcceleration = forwardAccelerationMps2.isFinite
        ? forwardAccelerationMps2
            .clamp(-_maximumAccelerationMps2, _maximumAccelerationMps2)
            .toDouble()
        : 0.0;
    var propagated = (previous + usableAcceleration * dtSeconds)
        .clamp(0.0, maximumSpeedMps)
        .toDouble();
    final cnnUsable = cnnTrusted &&
        cnnSpeedMps != null &&
        cnnSpeedMps.isFinite &&
        cnnSpeedMps >= 0 &&
        cnnSpeedMps <= maximumSpeedMps;
    if (cnnUsable) {
      final previousCnnSpeed = _lastCnnSpeedMps;
      _lastCnnSpeedMps = cnnSpeedMps;
      if (previousCnnSpeed != null) {
        final inertialDelta = usableAcceleration * dtSeconds;
        final modelDelta = cnnSpeedMps - previousCnnSpeed;
        final maximumResidual = _maximumModelAccelerationMps2 * dtSeconds;
        final residual = (modelDelta - inertialDelta)
            .clamp(-maximumResidual, maximumResidual)
            .toDouble();
        propagated = (propagated + residual * _modelResidualGain)
            .clamp(0.0, maximumSpeedMps)
            .toDouble();
      }
    } else {
      // Require two consecutive trusted windows after a quality lapse before
      // a CNN derivative can influence the physical state again.
      _lastCnnSpeedMps = null;
    }
    // Avoid a signed negative zero in UI and telemetry JSON.
    _speedMps = math.max(0.0, propagated);
    return _speedMps;
  }
}
