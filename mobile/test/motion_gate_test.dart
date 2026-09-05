import 'package:flutter_test/flutter_test.dart';
import 'package:meridian/domain/telemetry_snapshot.dart';
import 'package:meridian/services/motion_gate.dart';

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
    expect(
      latch.update(
        gnssEnabled: true,
        gnssFixFresh: true,
        gnssReportsMotion: true,
      ),
      isTrue,
    );
    expect(
      latch.update(
        gnssEnabled: false,
        gnssFixFresh: true,
        gnssReportsMotion: false,
      ),
      isTrue,
    );
  });
}
