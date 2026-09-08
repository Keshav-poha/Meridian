import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:meridian/domain/telemetry_snapshot.dart';
import 'package:meridian/services/imu_preprocessor.dart';

void main() {
  test('removes gravity and resolves yaw from GNSS vehicle kinematics', () {
    final preprocessor = VehicleFramePreprocessor();
    final start = DateTime(2026, 9, 5);
    const speedMps = 30.0;
    const headingStepDeg = 8.0;
    final lateralAcceleration = speedMps * headingStepDeg * math.pi / 180.0;

    // The phone's levelled +X axis is vehicle-right: a +90° yaw transform
    // maps it to vehicle-forward/right coordinates. The deliberately absurd
    // magnetic field demonstrates that compass distortion is not calibration
    // evidence anymore.
    preprocessor.update(
      accelerometer: const Axis3(0, 0, 9.80665),
      gyroscope: const Axis3(0, 0, 0),
      magnetometer: const Axis3(500, -700, 300),
      timestamp: start,
    );
    preprocessor.setGnssReference(
      speedMps: speedMps,
      headingDeg: 0,
      accuracyM: 5,
      timestamp: start,
    );
    for (var index = 1; index <= 24; index++) {
      final timestamp = start.add(Duration(seconds: index));
      preprocessor.update(
        accelerometer: Axis3(lateralAcceleration, 0, 9.80665),
        gyroscope: Axis3(0, 0, headingStepDeg * math.pi / 180.0),
        magnetometer: const Axis3(500, -700, 300),
        timestamp: timestamp,
      );
      preprocessor.setGnssReference(
        speedMps: speedMps,
        headingDeg: index * headingStepDeg,
        accuracyM: 5,
        timestamp: timestamp,
      );
    }

    final frame = preprocessor.update(
      accelerometer: const Axis3(2, 0, 9.80665),
      gyroscope: const Axis3(0.01, 0.02, 0.03),
      magnetometer: const Axis3(500, -700, 300),
      timestamp: start.add(const Duration(seconds: 25)),
    );

    expect(frame.mountCalibrated, isTrue);
    expect(frame.linearAcceleration.x, closeTo(0, 0.12));
    expect(frame.linearAcceleration.y, closeTo(1.92, 0.12));
    expect(frame.linearAcceleration.z, closeTo(0, 0.03));
    expect(frame.gyroscope.x, closeTo(-0.02, 0.004));
    expect(frame.gyroscope.y, closeTo(0.01, 0.004));
  });

  test('operates mount-independently on a straight GNSS segment', () {
    final preprocessor = VehicleFramePreprocessor();
    final start = DateTime(2026, 9, 5);
    for (var index = 0; index < 8; index++) {
      final timestamp = start.add(Duration(seconds: index));
      preprocessor.update(
        accelerometer: const Axis3(0.2, 0, 9.80665),
        gyroscope: const Axis3(0, 0, 0),
        magnetometer: const Axis3(30, 0, 0),
        timestamp: timestamp,
      );
      preprocessor.setGnssReference(
        speedMps: 5,
        headingDeg: 0,
        accuracyM: 5,
        timestamp: timestamp,
      );
    }

    expect(preprocessor.isMountCalibrated, isTrue);
    expect(preprocessor.mountState, MountCalibrationState.calibrated);
  });
}
