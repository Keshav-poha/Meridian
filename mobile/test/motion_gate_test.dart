import 'package:flutter_test/flutter_test.dart';
import 'package:meridian/domain/telemetry_snapshot.dart';
import 'package:meridian/services/motion_gate.dart';
import 'package:meridian/services/velocity_model_quality.dart';

void main() {
  test('holds navigation speed at zero after a stable parked IMU window', () {
    final gate = MotionGate(requiredStillSamples: 3);
    var stationary = false;
    for (var index = 0; index < 3; index++) {
      stationary = gate.update(
        accelerometer: const Axis3(0.04, -0.02, 9.807),
        gyroscope: const Axis3(0.001, -0.001, 0.0),
        gnssReportsMotion: false,
      );
    }
    expect(stationary, isTrue);
  });

  test('does not gate a GNSS-confirmed moving vehicle', () {
    final gate = MotionGate(requiredStillSamples: 1);
    final stationary = gate.update(
      accelerometer: const Axis3(0.02, 0.01, 9.807),
      gyroscope: const Axis3(0.001, 0.0, 0.0),
      gnssReportsMotion: true,
    );
    expect(stationary, isFalse);
  });

  test('requires GNSS-confirmed motion before an outage can use CNN speed', () {
    final latch = VehicleMotionLatch();
    expect(
      latch.update(
        gnssEnabled: true,
        gnssFixFresh: true,
        gnssReportsMotion: false,
      ),
      isFalse,
    );
    for (var index = 0; index < 3; index++) {
      latch.update(
        gnssEnabled: true,
        gnssFixFresh: true,
        gnssReportsMotion: true,
      );
    }
    expect(latch.armed, isTrue);
    expect(
      latch.update(
        gnssEnabled: false,
        gnssFixFresh: true,
        gnssReportsMotion: false,
      ),
      isTrue,
    );
  });

  test('enters a cooldown after a shock instead of reporting a speed', () {
    final gate = MotionGate(requiredStillSamples: 1, shockCooldownSamples: 2);
    expect(
      gate.update(
        accelerometer: const Axis3(0, 0, 18),
        gyroscope: const Axis3(0, 0, 0),
        gnssReportsMotion: false,
      ),
      isFalse,
    );
    expect(gate.inShockCooldown, isTrue);
    expect(
      gate.update(
        accelerometer: const Axis3(0, 0, 9.80665),
        gyroscope: const Axis3(0, 0, 0),
        gnssReportsMotion: false,
      ),
      isFalse,
    );
  });

  test('rejects a persistently out-of-distribution phone IMU window', () {
    final inDistribution = VelocityModelQuality.fromNormalizedWindow(
      List<List<double>>.generate(20, (_) => List<double>.filled(9, 1.0)),
    );
    final shiftedMagnetometer = VelocityModelQuality.fromNormalizedWindow(
      List<List<double>>.generate(
        20,
        (_) => <double>[0.2, -0.1, 0.3, 0.2, 0.1, -0.2, -2.1, 9.7, -2.9],
      ),
    );

    expect(inDistribution.isInDistribution, isTrue);
    expect(shiftedMagnetometer.isInDistribution, isFalse);
  });

  test('waits for a GNSS fix timestamped after a loss before recovery', () {
    final gate = GnssRecoveryGate();
    final loss = DateTime.utc(2026, 9, 5, 10);

    gate.requireFixAfter(loss);

    expect(gate.awaitingFreshFix, isTrue);
    expect(gate.accept(loss), isFalse);
    expect(gate.accept(loss.subtract(const Duration(milliseconds: 1))), isFalse);
    expect(gate.awaitingFreshFix, isTrue);
    expect(gate.accept(loss.add(const Duration(milliseconds: 1))), isTrue);
    expect(gate.awaitingFreshFix, isFalse);
  });
}
